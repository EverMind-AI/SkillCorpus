"""Round B new tests: LLM quality judge + compute_quality new weights."""

from __future__ import annotations

import json
import sqlite3

from skill_library.core.store import SkillRecord
from skill_library.metadata import compute_quality
from skill_library.metadata import (
    LLMQualityJudge, QualityJudgment, _build_prompt, synthesize_score,
)


class _FakeLLM:
    """v2 3-dim judge stub — returns {utility, robustness, safety, flags, reason}.

    The judge synthesizes the composite ``score`` from these via
    ``synthesize_score``; tests assert against that function rather than a
    hard-coded number so they track the formula."""

    def __init__(self, utility: int = 8, robustness: int = 7, safety: int = 9,
                 flags: list[str] | None = None, reason: str = "fake"):
        self.utility = utility
        self.robustness = robustness
        self.safety = safety
        self.flags = flags or []
        self._reason = reason
        self.call_count = 0

    def chat(self, messages, response_format=None, max_tokens=None):
        self.call_count += 1
        return json.dumps({
            "utility": self.utility, "robustness": self.robustness,
            "safety": self.safety, "flags": self.flags, "reason": self._reason,
        })


def _fresh_conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


def _make_rec(content_hash: str = "h1", body: str = "long body " * 100) -> SkillRecord:
    return SkillRecord(
        skill_id="sid", name="demo", description="demo desc " * 10,
        body=body, content_hash=content_hash,
    )


# ----------------------------------------------------------------------
# LLMQualityJudge
# ----------------------------------------------------------------------

def test_quality_judge_cache_and_normalize():
    judge = LLMQualityJudge(
        _FakeLLM(utility=8, robustness=7, safety=9, reason="solid"), _fresh_conn())
    rec = _make_rec(content_hash="h_abc")
    expected = synthesize_score(8, 7, 9, [])

    j1 = judge.score(rec)
    assert j1 is not None
    assert j1.score == expected
    assert j1.normalized == expected / 10.0
    assert (j1.utility, j1.robustness, j1.safety) == (8, 7, 9)
    assert j1.cached is False
    assert judge.llm.call_count == 1

    # the second call hits the cache
    j2 = judge.score(rec)
    assert j2.cached is True
    assert j2.normalized == expected / 10.0
    assert judge.llm.call_count == 1


def test_quality_judge_hard_gate_and_fails_gracefully():
    # safety < 3 → composite 0 (hard gate)
    judge = LLMQualityJudge(_FakeLLM(utility=9, robustness=9, safety=1), _fresh_conn())
    j = judge.score(_make_rec(content_hash="h_gate"))
    assert j is not None
    assert j.score == 0.0
    assert j.normalized == 0.0

    # out-of-range sub-score (utility=13) → parse rejects → None
    judge_oob = LLMQualityJudge(_FakeLLM(utility=13), _fresh_conn())
    assert judge_oob.score(_make_rec(content_hash="h_oob")) is None

    # LLM returns non-JSON → None
    class _BadLLM:
        def chat(self, *a, **kw): return None
    bad_judge = LLMQualityJudge(_BadLLM(), _fresh_conn())
    assert bad_judge.score(_make_rec(content_hash="h2")) is None


def test_compute_no_cache_bypasses_db():
    conn = _fresh_conn()
    judge = LLMQualityJudge(_FakeLLM(utility=6, robustness=6, safety=6), conn)

    rec = _make_rec(content_hash="h_no_cache")
    j = judge.compute_no_cache(rec)
    assert j is not None
    assert j.score == synthesize_score(6, 6, 6, [])
    # cache is empty (compute_no_cache does not write the DB)
    row = conn.execute("SELECT COUNT(*) FROM quality_judgments").fetchone()
    assert row[0] == 0

    # main thread puts it manually
    judge.cache_put(rec.content_hash, j)
    row = conn.execute("SELECT COUNT(*) FROM quality_judgments").fetchone()
    assert row[0] == 1


def test_get_cached_score():
    judge = LLMQualityJudge(_FakeLLM(utility=8, robustness=8, safety=8), _fresh_conn())
    rec = _make_rec(content_hash="h_get")
    expected = synthesize_score(8, 8, 8, [])
    assert judge.get_cached_score(rec.content_hash) is None
    judge.score(rec)
    assert judge.get_cached_score(rec.content_hash) == expected / 10.0


# ----------------------------------------------------------------------
# compute_quality new weights
# ----------------------------------------------------------------------

def test_compute_quality_llm_dominates():
    """llm_score=1.0 should significantly raise the final quality, even when the source is low."""
    kw = dict(
        source="awesome:foo/bar",  # default weight custom=0.5
        source_weights={"anthropics": 1.0, "custom": 0.5},
        body_len=3000, desc_len=150, frontmatter={"tags": ["a"], "license": "MIT"},
        has_scripts=False, has_references=False, safety_flags=[],
    )
    q_no_llm = compute_quality(**kw, llm_score=None)
    q_high_llm = compute_quality(**kw, llm_score=1.0)
    q_low_llm = compute_quality(**kw, llm_score=0.1)

    # LLM high should be significantly above no_llm, and significantly above LLM low
    assert q_high_llm > q_no_llm + 0.05
    assert q_high_llm > q_low_llm + 0.2


def test_compute_quality_source_weight_reduced():
    """Switching the same skill from custom (0.5) to anthropics (1.0), the delta should stay under ~0.2 (old version ~0.35)."""
    base = dict(
        source_weights={"anthropics": 1.0, "custom": 0.5},
        body_len=3000, desc_len=150, frontmatter={},
        has_scripts=False, has_references=False, safety_flags=[],
        llm_score=0.7,  # enable the new weights
    )
    q_anth = compute_quality(source="anthropics", **base)
    q_cust = compute_quality(source="custom", **base)
    # source contributes only 0.15 weight, so the delta should be <= 0.15 * (1.0 - 0.5) = 0.075
    assert q_anth - q_cust <= 0.08
    assert q_anth > q_cust


def test_compute_quality_fallback_without_llm():
    """llm_score=None uses the old weights (source 0.35), ensuring compatibility."""
    q = compute_quality(
        source="anthropics",
        source_weights={"anthropics": 1.0, "custom": 0.5},
        body_len=3000, desc_len=150, frontmatter={"tags": ["a"]},
        has_scripts=False, has_references=False, safety_flags=[],
        llm_score=None,
    )
    # anthropics + 3000 chars + reasonable desc, should be >= 0.6
    assert q >= 0.6


def test_compute_quality_safety_blocked():
    q = compute_quality(
        source="anthropics",
        source_weights={"anthropics": 1.0, "custom": 0.5},
        body_len=3000, desc_len=150, frontmatter={},
        has_scripts=True, has_references=True,
        safety_flags=["blocked.malware"],
        llm_score=1.0,  # even with a perfect LLM score, blocked forces 0
    )
    assert q == 0.0


if __name__ == "__main__":
    test_quality_judge_cache_and_normalize()
    test_quality_judge_hard_gate_and_fails_gracefully()
    test_compute_no_cache_bypasses_db()
    test_get_cached_score()
    test_compute_quality_llm_dominates()
    test_compute_quality_source_weight_reduced()
    test_compute_quality_fallback_without_llm()
    test_compute_quality_safety_blocked()
    print("ALL ROUND-B TESTS PASSED")
