"""curate.parse — SKILL.md frontmatter parsing + validation (pure, no LLM)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


# ======================== SKILL.md parsing ========================
"""SKILL.md parsing: frontmatter (YAML) + body.

Prefer PyYAML (supports nested fields such as antigravity's metadata.openclaw);
fall back to simple line-by-line parsing (compatible with the OpenSpace skill_utils style).
"""



try:
    import yaml  # PyYAML
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\s*\n?(.*)", re.DOTALL)

SKILL_FILENAME = "SKILL.md"
SKILL_FILENAME_LOWER = "skill.md"


class ParseError(ValueError):
    """SKILL.md parsing failed."""


def parse_skill_md(content: str) -> tuple[dict[str, Any], str]:
    """Parse SKILL.md text, returning (frontmatter_dict, body).

    Body excludes the frontmatter and the delimiters.
    """
    # Normalize a UTF-8 BOM + CRLF/CR line endings first. The frontmatter regex
    # is LF-only and str.lstrip() does not treat the BOM as whitespace, so a
    # Windows-authored SKILL.md would otherwise be silently REJECTED_PARSE.
    content = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if not content.lstrip().startswith("---"):
        raise ParseError("no frontmatter (content must start with '---')")

    # Ensure matching starts from ---
    content = content.lstrip()
    m = _FRONTMATTER_RE.match(content)
    if m is None:
        raise ParseError("malformed frontmatter (no closing '---')")

    fm_raw = m.group(1)
    body = m.group(2)

    if _HAS_YAML:
        try:
            fm = yaml.safe_load(fm_raw) or {}
        except yaml.YAMLError as e:
            raise ParseError(f"invalid YAML: {e}") from e
    else:
        fm = _simple_parse(fm_raw)

    if not isinstance(fm, dict):
        raise ParseError("frontmatter is not a dict")

    return fm, body


def _simple_parse(fm_raw: str) -> dict[str, Any]:
    """Fallback naive key:value parsing (single level only)."""
    fm: dict[str, Any] = {}
    for line in fm_raw.split("\n"):
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        v = v.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        if k:
            fm[k] = v
    return fm


def parse_skill_file(path: Path) -> tuple[dict[str, Any], str]:
    """Read and parse SKILL.md from a path."""
    content = path.read_text(encoding="utf-8", errors="replace")
    return parse_skill_md(content)


def find_skill_md(skill_dir: Path) -> Path | None:
    """Find SKILL.md in skill_dir (case-insensitive)."""
    for name in (SKILL_FILENAME, SKILL_FILENAME_LOWER):
        candidate = skill_dir / name
        if candidate.is_file():
            return candidate
    return None


class ValidationError(ValueError):
    """Skill metadata validation failed."""


def validate_skill(fm: dict[str, Any], body: str) -> None:
    """Validate required fields (agentskills.io spec).

    Required: name, description
    Raises: ValidationError
    """
    name = fm.get("name")
    if not name or not isinstance(name, str):
        raise ValidationError("missing or invalid 'name'")
    description = fm.get("description")
    if not description or not isinstance(description, str):
        raise ValidationError("missing or invalid 'description'")

    # name spec: lowercase + digit + hyphen, ≤ 64
    if not re.fullmatch(r"[a-z0-9][a-z0-9\-]{0,63}", name):
        raise ValidationError(
            f"name '{name}' does not match slug spec (lowercase letters/digits/hyphens, ≤64)"
        )

