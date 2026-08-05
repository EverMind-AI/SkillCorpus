"""Producer integration-boundary smoke — several regressions in one self-contained
pass (no network / no real LLM / no real embedder):

  Phase 1  active-column backfill        — after an old library is upgraded, active
                                            is all 0; license_audit.cmd_activate only
                                            activates (never deactivates) per the GREEN whitelist
  Phase 3  quality cache hit             — hit by content_hash (judge frozen, no version key)
  Phase 4  dedup_pass name_hash cosine   — name_hash pairs use real cosine, not forced to 1.0
                                            (historically 68% false-positive auto-merges)
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from argparse import Namespace
from pathlib import Path

from skillcorpus import SkillLibrary
from skillcorpus.core.models import SkillRecord
from skillcorpus.core.hashing import name_hash, cosine_sim
from skillcorpus.curate import license_audit
from skillcorpus.curate.dedup_pass import _collect_candidates
from skillcorpus.curate.quality import LLMQualityJudge, QualityJudgment
from skillcorpus.curate.license import GREEN_LICENSES


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

        # ── Phase 1: active upgrade backfill ─────────────────────────────────────
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

        # ── Phase 4: dedup_pass name_hash uses real cosine ───────────────────────
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
            f"name_hash pair cos={cos} should be a real low cosine (~{real:.3f}), not forced to 1.0"
        assert abs(cos - real) < 0.05, f"cos {cos} should ≈ real cosine {real}"

        lib.close()


if __name__ == "__main__":
    test_upgrade_smoke()
    print("PRODUCER UPGRADE SMOKE TEST PASSED")
