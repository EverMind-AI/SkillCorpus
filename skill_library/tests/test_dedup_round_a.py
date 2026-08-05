"""Round A new-feature tests: canonical name + embedding nearest-neighbor + LLM dedup judge + supersede."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from skill_library import SkillLibrary, IngestStatus
from skill_library.core.hashing import canonical_name, name_hash
from skill_library.curate.dedup import LLMDupJudge, DupJudgment, _pair_key
from skill_library.core.models import SkillRecord


# ----------------------------------------------------------------------
# A-1: canonical name
# ----------------------------------------------------------------------

def test_canonical_name_absorbs_separators():
    """Case / separators / punctuation / extra whitespace all normalize to a consistent form."""
    expected = "react hooks"
    for variant in [
        "react-hooks", "React Hooks", "react_hooks",
        "React.Hooks", "React.Hooks!!", "react  hooks",
        "React--Hooks", "react/hooks", "REACT@HOOKS",
    ]:
        assert canonical_name(variant) == expected, f"failed: {variant!r}"


def test_name_hash_collides_on_canonical_variants():
    h = name_hash("react-hooks")
    for variant in ["React Hooks", "react_hooks", "React.Hooks"]:
        assert name_hash(variant) == h, f"hash mismatch: {variant!r}"
    # genuinely different names should not collide
    assert name_hash("react-router") != h


# ----------------------------------------------------------------------
# A-3: LLM dup judge cache
# ----------------------------------------------------------------------

class _FakeLLM:
    """Fake LLM that always returns the given is_duplicate value."""
    def __init__(self, is_dup: bool, conf: float = 0.9, reason: str = "fake"):
        self._is_dup = is_dup
        self._conf = conf
        self._reason = reason
        self.call_count = 0

    def chat(self, messages, response_format=None, max_tokens=None):
        self.call_count += 1
        import json
        return json.dumps({
            "is_duplicate": self._is_dup,
            "confidence": self._conf,
            "reason": self._reason,
        })


def _make_rec(sid: str, name: str, body: str, source: str = "custom",
              content_hash: str = "", quality: float = 0.5) -> SkillRecord:
    return SkillRecord(
        skill_id=sid, name=name, description="desc " * 10, body=body,
        source=source, content_hash=content_hash or f"hash_{sid}",
        name_hash=name_hash(name), quality_score=quality,
    )


def test_dup_judge_caches_verdict():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    llm = _FakeLLM(is_dup=True)
    judge = LLMDupJudge(llm, conn)

    a = _make_rec("a", "a", "body a body a", content_hash="h_a")
    b = _make_rec("b", "b", "body b body b", content_hash="h_b")

    j1 = judge.is_duplicate(a, b)
    assert j1.is_duplicate is True
    assert j1.cached is False
    assert llm.call_count == 1

    # the second call should hit the cache and not call the LLM again
    j2 = judge.is_duplicate(a, b)
    assert j2.is_duplicate is True
    assert j2.cached is True
    assert llm.call_count == 1  # unchanged

    # reversed argument order should also hit the same cache key
    j3 = judge.is_duplicate(b, a)
    assert j3.cached is True
    assert llm.call_count == 1


def test_dup_judge_returns_not_dup_on_llm_failure():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    class _BadLLM:
        def chat(self, *a, **kw):
            return None
    judge = LLMDupJudge(_BadLLM(), conn)

    a = _make_rec("a", "a", "body a", content_hash="h_a")
    b = _make_rec("b", "b", "body b", content_hash="h_b")
    j = judge.is_duplicate(a, b)
    assert j.is_duplicate is False
    assert j.cached is False


def test_pair_key_order_independent():
    assert _pair_key("abc", "xyz") == _pair_key("xyz", "abc")


# ----------------------------------------------------------------------
# A-4: cross-source merge path — end-to-end (using a Fake LLM instead)
# ----------------------------------------------------------------------

_SKILL_MD_TEMPLATE = """---
name: {name}
description: {desc}
---

# {name}

{body}
"""


def _write_skill(dir_: Path, dirname: str, name: str, desc: str, body: str) -> Path:
    d = dir_ / dirname
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        _SKILL_MD_TEMPLATE.format(name=name, desc=desc, body=body),
        encoding="utf-8",
    )
    return d


def test_cross_source_name_collision_triggers_merge():
    """Skills with the same slug-name uploaded from different sources trigger supersede once the LLM is enabled.

    Note: the skill name must follow the agentskills.io slug convention (lowercase + hyphens).
    Canonical normalization of case/underscore/whitespace is covered in
    test_canonical_name_absorbs_separators; here we only test the cross-source
    collision path for valid slugs.
    """
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src"
        a_dir = _write_skill(
            src, "a", "pdf-gen",
            "Generate PDF reports with python reportlab for financial data.",
            "Use reportlab.pdfgen. Create canvas. Draw rows. Save PDF. " * 20,
        )
        b_dir = _write_skill(
            src, "b", "pdf-gen",
            "Generate PDF reports with python reportlab for financial data.",
            "Use reportlab. canvas. Save pdf. " * 25,
        )

        lib = SkillLibrary(Path(tmp) / "lib").open()
        # inject a Fake dup judge that always returns True (avoid calling a real LLM)
        lib.dup_judge = LLMDupJudge(_FakeLLM(is_dup=True), lib.store._connect())
        lib.ingester.dup_judge = lib.dup_judge

        r1 = lib.add(a_dir, source="anthropics")
        assert r1.status == IngestStatus.ADDED

        r2 = lib.add(b_dir, source="awesome:someone/repo")
        # anthropics source_weight=1.0 vs awesome default 0.5, anthropics wins
        assert r2.status in (IngestStatus.MERGED_KEPT_OLD,
                             IngestStatus.MERGED_KEPT_NEW), \
            f"expected merge, got {r2.status}"

        # only one active record remains in the library
        active = lib.list(limit=100)
        assert len(active) == 1
        winner = active[0]
        assert winner.superseded_by is None

        # MERGED_KEPT_NEW: the loser was ingested and then superseded (leaves a trace)
        # MERGED_KEPT_OLD: new is the loser, never ingested, no supersede record
        conn = lib.store._connect()
        row = conn.execute(
            "SELECT skill_id, superseded_by FROM skills "
            "WHERE superseded_by IS NOT NULL"
        ).fetchone()
        if r2.status == IngestStatus.MERGED_KEPT_NEW:
            assert row is not None
            assert row["superseded_by"] == winner.skill_id
        else:
            # MERGED_KEPT_OLD: r2's new is dropped, the winner is the original anthropics
            assert winner.source == "anthropics"
        lib.close()


def test_same_source_same_name_still_overwrites():
    """Same source + same canonical name keeps the original overwrite behavior (not supersede)."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src"
        a = _write_skill(
            src, "v1", "pdf-gen",
            "Generate PDF reports with python reportlab for financial data.",
            "Use reportlab. " * 30,
        )
        import shutil
        shutil.rmtree(a)
        b = _write_skill(
            src, "v2", "pdf-gen",
            "Generate PDF reports with python reportlab for financial data (v2).",
            "Use reportlab. Canvas. Rect. Save. " * 30,
        )

        lib = SkillLibrary(Path(tmp) / "lib").open()
        lib.ingester._dedup_enabled = False
        lib.ingester.dup_judge = None

        r1 = lib.add(b, source="custom")
        assert r1.status == IngestStatus.ADDED
        first_id = r1.record.skill_id

        c = _write_skill(
            Path(tmp) / "src2", "v3", "pdf-gen",
            "Generate PDF reports with python reportlab.",
            "A different long enough body. " * 40,
        )
        r2 = lib.add(c, source="custom")
        assert r2.status == IngestStatus.ADDED
        assert r2.record.skill_id == first_id, "same-source same-canonical should overwrite same id"
        assert lib.stats()["total"] == 1
        lib.close()


if __name__ == "__main__":
    test_canonical_name_absorbs_separators()
    test_name_hash_collides_on_canonical_variants()
    test_dup_judge_caches_verdict()
    test_dup_judge_returns_not_dup_on_llm_failure()
    test_pair_key_order_independent()
    test_cross_source_name_collision_triggers_merge()
    test_same_source_same_name_still_overwrites()
    print("ALL ROUND-A TESTS PASSED")
