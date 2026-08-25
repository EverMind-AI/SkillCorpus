"""Tests for PathGuard placeholder resolution ({{SKILL_DIR}} etc.)."""

from skillsearch.refs import resolve_placeholders


def test_skill_dir_and_output() -> None:
    out = resolve_placeholders(
        "python {{SKILL_DIR}}/scripts/x.py --out {{OUTPUT_DIR}}/r.csv",
        "/skills/foo",
        output_dir=".",
    )
    assert out == "python /skills/foo/scripts/x.py --out ./r.csv"


def test_named_skill_dir_resolves_to_sibling() -> None:
    out = resolve_placeholders("see {{SKILL_DIR:other}}/refs/a.md", "/skills/foo")
    assert out == "see /skills/other/refs/a.md"


def test_agent_state_and_home() -> None:
    out = resolve_placeholders(
        "cat {{AGENT_STATE_DIR}}/auth/x > {{HOME}}/.cache/y",
        "/skills/foo",
        state_dir="/root/.openclaw",
        home_dir="/root",
    )
    assert out == "cat /root/.openclaw/auth/x > /root/.cache/y"


def test_state_and_home_fall_back_to_output_dir() -> None:
    out = resolve_placeholders(
        "cat {{AGENT_STATE_DIR}}/auth/x > {{HOME}}/.cache/y",
        "/skills/foo",
        output_dir=".",
    )
    assert out == "cat ./auth/x > ./.cache/y"


def test_missing_skill_dir_strips_prefix() -> None:
    out = resolve_placeholders("python {{SKILL_DIR}}/scripts/x.py", None)
    assert out == "python scripts/x.py"


def test_unknown_placeholder_untouched() -> None:
    out = resolve_placeholders("set {{YOUR_TOKEN}} and go", "/skills/foo")
    assert out == "set {{YOUR_TOKEN}} and go"


def test_no_placeholders_passthrough() -> None:
    assert resolve_placeholders("plain body", "/skills/foo") == "plain body"
