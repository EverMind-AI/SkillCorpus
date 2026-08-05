"""LLM classification + tag tests — pure functions + mock LLM, no DB dependency."""

from __future__ import annotations

from dataclasses import dataclass

from skill_library.metadata import (
    Classifier, VOCAB, _parse_classify_response,
)
from skill_library.metadata import extract_tags
from skill_library.core.store import Category, CATEGORIES


# ─────────────────────────────────────────────────────────────────────────
# Vocab & enum integrity
# ─────────────────────────────────────────────────────────────────────────

def test_vocab_size_16():
    assert len(VOCAB) == 16

def test_vocab_matches_enum():
    assert set(VOCAB) == set(CATEGORIES)
    assert set(VOCAB) == {c.value for c in Category}


# ─────────────────────────────────────────────────────────────────────────
# _parse_classify_response — JSON extraction robustness
# ─────────────────────────────────────────────────────────────────────────

def test_parse_clean_json():
    r = _parse_classify_response('{"category":"DEV","confidence":0.9,"reason":"API"}')
    assert r["category"] == "DEV"
    assert r["confidence"] == 0.9
    assert r["reason"] == "API"

def test_parse_code_fence():
    r = _parse_classify_response('```json\n{"category":"DATA","confidence":0.8,"reason":"DB"}\n```')
    assert r["category"] == "DATA"

def test_parse_with_thinking():
    r = _parse_classify_response('<think>thinking blah</think>{"category":"AUTH","confidence":0.95,"reason":"OAuth"}')
    assert r["category"] == "AUTH"

def test_parse_oov_category_rejected():
    assert _parse_classify_response('{"category":"FOO","confidence":0.9,"reason":"x"}') is None

def test_parse_lowercase_normalized():
    r = _parse_classify_response('{"category":"dev","confidence":0.8,"reason":"x"}')
    assert r["category"] == "DEV"

def test_parse_garbage_text():
    assert _parse_classify_response("I am not JSON") is None

def test_parse_confidence_clamping():
    r = _parse_classify_response('{"category":"OTHER","confidence":2.5,"reason":"x"}')
    assert r["confidence"] == 1.0
    r = _parse_classify_response('{"category":"OTHER","confidence":-0.1,"reason":"x"}')
    assert r["confidence"] == 0.0


# ─────────────────────────────────────────────────────────────────────────
# Classifier — mock LLM
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class _MockLLM:
    response: str | None = None
    def chat(self, msgs, response_format=None):
        return self.response


def test_classifier_success():
    llm = _MockLLM(response='{"category":"DEV","confidence":0.92,"reason":"SaaS"}')
    cls = Classifier(llm)
    r = cls.classify("stripe-payment", "Stripe SDK", "")
    assert r.category == "DEV"
    assert r.confidence == 0.92
    assert r.method == "llm"

def test_classifier_llm_unavailable():
    llm = _MockLLM(response=None)
    cls = Classifier(llm)
    r = cls.classify("x", "y", "")
    assert r.category == "OTHER"
    assert r.method == "fallback"

def test_classifier_unparseable():
    llm = _MockLLM(response="garbage no json here")
    cls = Classifier(llm)
    r = cls.classify("x", "y", "")
    assert r.category == "OTHER"
    assert r.method == "fallback"

def test_classifier_oov_category():
    llm = _MockLLM(response='{"category":"NONSENSE","confidence":0.9,"reason":"x"}')
    cls = Classifier(llm)
    r = cls.classify("x", "y", "")
    assert r.category == "OTHER"
    assert r.method == "fallback"


# ─────────────────────────────────────────────────────────────────────────
# extract_tags — frontmatter / name / description
# ─────────────────────────────────────────────────────────────────────────

def test_tags_from_frontmatter():
    tags = extract_tags("foo", "desc", {"tags": ["stripe", "payments"]})
    assert "stripe" in tags
    assert "payments" in tags

def test_tags_from_name_slug():
    tags = extract_tags("python-docx-generation", "x", {})
    assert "python" in tags
    assert "docx" in tags

def test_tags_dedup():
    tags = extract_tags("stripe-payment", "stripe payment", {"tags": ["stripe"]})
    assert tags.count("stripe") == 1

def test_tags_max_limit():
    tags = extract_tags(
        "a-b-c-d-e-f-g-h", "more words here for the test", {}, max_tags=3,
    )
    assert len(tags) <= 3

def test_tags_skip_stopwords():
    tags = extract_tags("skill-help-task", "Use this tool to help users.", {})
    forbidden = {"skill", "help", "tool", "use", "this", "users"}
    assert not (set(tags) & forbidden)


if __name__ == "__main__":
    import sys
    funcs = [
        f for name, f in globals().items()
        if name.startswith("test_") and callable(f)
    ]
    failed = 0
    for fn in funcs:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  ❌ {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ❌ {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(funcs)-failed}/{len(funcs)} passed")
    sys.exit(0 if failed == 0 else 1)
