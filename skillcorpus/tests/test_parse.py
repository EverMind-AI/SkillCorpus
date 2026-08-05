"""Parse / validate tests — pure functions, no DB dependency."""

from __future__ import annotations

from skillcorpus.curate.parse import (
    parse_skill_md, ParseError, ValidationError, validate_skill,
)


SAMPLE = """---
name: demo-skill
description: A demo skill that tests PDF generation using reportlab.
license: MIT
tags:
  - pdf
  - reportlab
---

# demo-skill

Body content here with enough length to pass checks.
Line 2.
Line 3.
"""


def test_parse_ok():
    fm, body = parse_skill_md(SAMPLE)
    assert fm["name"] == "demo-skill"
    assert "PDF" in fm["description"]
    assert fm["license"] == "MIT"
    assert fm["tags"] == ["pdf", "reportlab"]
    assert "Body content" in body


def test_parse_no_frontmatter():
    try:
        parse_skill_md("# no frontmatter here")
        assert False, "should have raised ParseError"
    except ParseError:
        pass


def test_parse_malformed_yaml():
    bad = "---\nname: [unclosed\n---\nbody"
    try:
        parse_skill_md(bad)
        assert False, "should have raised"
    except ParseError:
        pass


def test_validate_missing_name():
    try:
        validate_skill({"description": "ok " * 10}, "body body body")
        assert False, "should raise for missing name"
    except ValidationError:
        pass


def test_validate_bad_name_slug():
    try:
        validate_skill({"name": "Bad Name!", "description": "x" * 30}, "body")
        assert False, "non-slug name should be rejected"
    except ValidationError:
        pass


def test_validate_missing_description():
    try:
        validate_skill({"name": "demo"}, "body")
        assert False, "missing description should be rejected"
    except ValidationError:
        pass


def test_validate_overlong_description_tolerated():
    """description > 1024 does not raise (Round C: changed to a warning); the actual warning fires in the ingest flow."""
    validate_skill(
        {"name": "demo", "description": "x" * 1500},
        "body body body",
    )  # does not raise


def test_parse_bom_and_crlf():
    """P1-8: a UTF-8 BOM / CRLF / CR SKILL.md must parse, not REJECTED_PARSE.

    The frontmatter regex is LF-only and str.lstrip() does not strip the BOM,
    so Windows-authored files used to be silently rejected.
    """
    variants = {
        "BOM": "\ufeff" + SAMPLE,
        "CRLF": SAMPLE.replace("\n", "\r\n"),
        "CR": SAMPLE.replace("\n", "\r"),
        "BOM+CRLF": "\ufeff" + SAMPLE.replace("\n", "\r\n"),
    }
    for label, content in variants.items():
        fm, body = parse_skill_md(content)
        assert fm["name"] == "demo-skill", label
        assert fm["tags"] == ["pdf", "reportlab"], label
        assert "Body content" in body, label
        assert "\r" not in body, f"{label}: body still has CR"


if __name__ == "__main__":
    test_parse_ok()
    test_parse_no_frontmatter()
    test_parse_malformed_yaml()
    test_validate_missing_name()
    test_validate_bad_name_slug()
    test_validate_missing_description()
    test_validate_overlong_description_tolerated()
    test_parse_bom_and_crlf()
    print("ALL PARSE TESTS PASSED")
