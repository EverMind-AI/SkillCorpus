"""Upgrade end-to-end smoke test — covers several integration-boundary regressions in one pass.

These pitfalls (migration path / recompute scripts / producer<->consumer label handshake) all live
at **integration boundaries** that ordinary unit tests don't reach. This file uses a fully
self-contained (no network / no real LLM / no real embedder) mini library, walks the upgrade path
once, and asserts that each fix point has not regressed:

  Phase 1  active-column upgrade backfill (🔴#2)  — after upgrading an old library active is all 0;
                                                     cmd_activate only activates (never deactivates)
                                                     per the GREEN whitelist
  Phase 2  export default label           (🔴#5)  — without --embedding-model the label comes from
                                                     config.yaml (= consumer-side config), not hardcoded
  Phase 2  export non-empty               (🔴#2)  — after backfill mass_library.db has rows (empty before the fix)
  Phase 3  quality cache hit              (🔴#3)  — hit by content_hash (judge frozen, no version key)
  Phase 4  rescan_dedup name_hash         (🔴#1)  — name_hash pairs use real cosine, no longer forced to 1.0
                                                     auto-merge (historically 7148/68% false positives)
  Phase 5  license whitelist consistency  (🔴#4)  — JSON green_categories == code GREEN set
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from argparse import Namespace
from pathlib import Path

from skill_library import SkillLibrary
from skill_library.core.models import SkillRecord
from skill_library.core.hashing import name_hash, cosine_sim
from skill_library.curate import license_audit
from skill_library import export as export_mod
from skill_library.export import export, _config_embedding
from skill_library.curate.dedup_pass import _collect_candidates
from skill_library.curate.quality import (
    LLMQualityJudge, QualityJudgment,
)
from skill_library.curate.license import GREEN_LICENSES


def _rec(sid, name, source, body, ch) -> SkillRecord:
    return SkillRecord(
        skill_id=sid, name=name, description="desc " * 10, body=body,
        source=source, content_hash=ch, name_hash=name_hash(name),
        quality_score=0.6,
    )


def _emb(dim, half):
    """Unit direction vector: half=0 → first half=1, half=1 → second half=1. The two have cosine ≈ 0."""
    v = [0.0] * dim
    lo, hi = (0, dim // 2) if half == 0 else (dim // 2, dim)
    for i in range(lo, hi):
        v[i] = 1.0
    return v


def _build_lib(root: Path):
    """Mini library: 2 GREEN source rows + 1 non-GREEN row, all with embeddings.

    None of the source names are in the real license_safe_sources.json → store.insert
    leaves them all active=0, which exactly reproduces the 'active column defaults to
    all 0 after upgrading an old library' state, with no manual tweaking needed.
    """
    lib = SkillLibrary(root).open()
    dim = lib.store.embedding_dim
    lib.store.insert(_rec("g1", "alpha-skill", "greensrc/repo",
                          "body alpha " * 20, "h_g1"), embedding=_emb(dim, 0))
    lib.store.insert(_rec("g2", "beta-skill", "greensrc/repo",
                          "body beta " * 20, "h_g2"), embedding=_emb(dim, 1))
    lib.store.insert(_rec("r1", "gamma-skill", "redsrc/repo",
                          "body gamma " * 20, "h_r1"), embedding=_emb(dim, 0))
    return lib, dim


def _active_count(db_path: Path) -> int:
    con = sqlite3.connect(db_path)
    try:
        return con.execute(
            "SELECT COUNT(*) FROM skills WHERE active=1 AND deleted=0"
        ).fetchone()[0]
    finally:
        con.close()


def test_upgrade_smoke():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        lib, dim = _build_lib(tmp / "lib")
        db_path = lib.lib_root / "index.db"

        # ── Phase 1: active upgrade backfill (🔴#2) ──────────────────────────────
        # post-upgrade symptom: all rows active=0 → export would produce an empty library.
        assert _active_count(db_path) == 0, "upgrade state should have active all 0 (precondition to reproduce the bug)"

        green_json = tmp / "green.json"
        green_json.write_text(json.dumps({
            "green_categories": sorted(GREEN_LICENSES),
            "sources": ["greensrc/repo"],   # only greensrc is GREEN
        }), encoding="utf-8")

        rc = license_audit.cmd_activate(Namespace(
            json=str(green_json), db=str(db_path), dry_run=False))
        assert rc == 0
        # the 2 GREEN-source rows get activated; non-GREEN r1 stays 0 (activate only, never deactivate)
        con = sqlite3.connect(db_path)
        act = {r[0]: r[1] for r in con.execute(
            "SELECT skill_id, active FROM skills").fetchall()}
        con.close()
        assert act == {"g1": 1, "g2": 1, "r1": 0}, f"activate result wrong: {act}"

        # idempotent: running again does not change the result
        license_audit.cmd_activate(Namespace(
            json=str(green_json), db=str(db_path), dry_run=False))
        assert _active_count(db_path) == 2, "activate is not idempotent"

        # ── Phase 2: export default label comes from config + non-empty (🔴#5 / 🔴#2) ──
        mass = tmp / "mass_library.db"
        cfg_model, _cfg_dim = _config_embedding()
        export(lib.lib_root, mass)   # no embedding_model passed → should read config.yaml

        mcon = sqlite3.connect(mass)
        try:
            n = mcon.execute("SELECT COUNT(*) FROM skills").fetchone()[0]
            labels = {r[0] for r in mcon.execute(
                "SELECT DISTINCT embedding_model FROM skills "
                "WHERE embedding IS NOT NULL").fetchall()}
        finally:
            mcon.close()
        assert n == 2, f"after backfill export should have 2 rows, got {n} (🔴#2: empty library after upgrade)"
        assert labels == {cfg_model}, \
            f"embedding_model should = config '{cfg_model}', got {labels} (🔴#5)"

        # ── Phase 3: quality cache hit by content_hash (judge frozen, no version key) ──
        qconn = sqlite3.connect(":memory:")
        qconn.row_factory = sqlite3.Row

        class _FakeLLM:
            def chat(self, *a, **k):
                return json.dumps({"utility": 8, "robustness": 7,
                                   "safety": 9, "flags": [], "reason": "x"})

        judge = LLMQualityJudge(_FakeLLM(), qconn)
        judge.cache_put("h_new", QualityJudgment(
            score=7.8, normalized=0.78, reason="new",
            utility=8, robustness=7, safety=9, flags=[]))
        assert judge._cache_get("h_new") is not None, "a cached content_hash should hit"
        assert judge._cache_get("h_absent") is None, "an uncached content_hash should miss"

        # ── Phase 4: rescan_dedup name_hash uses real cosine (🔴#1) ──────────
        # two cross-source skills with the same name but different content/embedding (cos≈0).
        ea, eb = _emb(dim, 0), _emb(dim, 1)
        lib.store.insert(_rec("d1", "dup-name", "srcA/repo",
                              "alpha content " * 20, "h_d1"), embedding=ea)
        lib.store.insert(_rec("d2", "dup-name", "srcB/repo",
                              "totally different " * 20, "h_d2"), embedding=eb)
        lib.dup_judge = None          # no LLM → only cos>=auto_cos auto-merges
        lib.ingester.dup_judge = None
        pairs = _collect_candidates(lib, min_cos=0.0, top_k=5, limit=None)
        key = tuple(sorted(["d1", "d2"]))
        assert key in pairs, f"name_hash pair was not collected: {list(pairs)}"
        cos, trigger = pairs[key]
        real = cosine_sim(ea, eb)
        assert cos < 0.99, \
            f"name_hash pair cos={cos} should be a real low cosine (~{real:.3f}), not forced to 1.0 (🔴#1)"
        assert abs(cos - real) < 0.05, f"cos {cos} should ≈ real cosine {real}"

        # ── Phase 5: license whitelist ↔ code GREEN set consistency (🔴#4) ────────
        real_json = json.loads(
            (Path(export_mod.__file__).resolve().parent
             / "license_safe_sources.json").read_text(encoding="utf-8"))
        assert set(real_json["green_categories"]) == set(GREEN_LICENSES), \
            "license_safe_sources.json green_categories does not match code GREEN set (🔴#4)"
        assert "0BSD" in real_json["green_categories"], "0BSD missing (🔴#4)"

        lib.close()


if __name__ == "__main__":
    test_upgrade_smoke()
    print("ITER3 UPGRADE SMOKE TEST PASSED")
