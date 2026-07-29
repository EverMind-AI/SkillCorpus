"""rules.py — pure rule stage (no LLM): parse + safety + license.

Three parts: SKILL.md frontmatter parsing, safety regex hard-blocking, and the license GREEN gate.
"""
from __future__ import annotations

import csv
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

    if len(description) > 1024:
        # agentskills.io cap; truncate but don't reject
        pass


# ======================== safety regex hard-block ========================
"""Safety check — regex hard-block (only 1 rule kept).

Only `blocked.*` rules trigger rejection from ingestion.

History: there used to be 6 `suspicious.*` rules (keyword / secrets / crypto / webhook /
script / url_shortener) recorded as audit flags. On 2026-05-21 a stratified 100-skill
audit was run; the LLM judge labelled TP/FP, giving an **overall FP rate = 98%**:
  - suspicious.secrets   matched 48,962 skills (45% of the whole corpus), precision 0% (20/20 FP)
  - suspicious.webhook                                  precision 0%
  - suspicious.script / .url_shortener / .keyword       precision 0%
  - suspicious.crypto                                   precision 10% (2/20)
All were substring matches with no context awareness, and the LLM judge (3-dim + 19-flag)
already covers the real risks, so they were removed entirely.

The remaining fine-grained safety decisions are made by the LLM judge; the hard-gate is handled by quality.py:
  - numeric hard-gate: LLM safety < 3 → quality = 0
  - flag hard-gate: 5 flags (prompt_injection / cmd_injection / unsafe_exec /
                         auth_bypass / csam_risk) → quality = 0
"""



_SAFETY_RULES: list[tuple[str, re.Pattern]] = [
    ("blocked.malware", re.compile(r"(ClawdAuthenticatorTool)", re.IGNORECASE)),
]

_BLOCKING_FLAGS = frozenset({"blocked.malware"})


def check_safety(text: str) -> list[str]:
    """Return the names of triggered flags; an empty list means safe."""
    return [flag for flag, pat in _SAFETY_RULES if pat.search(text)]


def is_blocked(flags: list[str]) -> bool:
    """Any blocked.* flag means the skill is rejected from ingestion."""
    return any(f in _BLOCKING_FLAGS for f in flags)


# ======================== license GREEN gate ========================
"""License filter — enforce GREEN-only (commercially redistributable) active set.

Adds a hard-gate step on top of safety filtering: a skill is admitted
to the released `active` set only if its source repository's
GitHub-API `spdx_id` falls in the GREEN allow-list, OR its per-skill
licence string normalises to a GREEN identifier.

Use:
    from skill_library.rules import (
        is_green_license,
        normalize_license,
        load_source_license_map,
    )

    src_lic = load_source_license_map("source_license_report.csv")
    # ... during ingest, after LLM judging:
    if not is_green_license(record, src_lic):
        record.active = 0
        record.reason = "non-green license"

The GREEN allow-list mirrors the consumer-side mass-pool policy
(see docs/15_consumer_skill_library_status.md) and matches the paper's
released artifact.
"""



# OSI-approved permissive licences that allow commercial
# redistribution without copyleft, share-alike, or non-commercial
# restrictions.  This is the strict GREEN set used by the consumer-side
# mass pool and enforced in the released SkillCorpus active set.
GREEN_LICENSES = frozenset({
    "Apache-2.0",
    "BSD",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "0BSD",
    "CC0-1.0",
    "ISC",
    "MIT",
    "MIT-0",
    "MPL-2.0",
    "Mulan-PSL-2.0",
    "Unlicense",
    "WTFPL",
})

# License strings that signal commercial-incompatible terms; explicit
# REJECT list for clarity (any unknown string also falls to NO_LICENSE).
RED_LICENSES = frozenset({
    "AGPL-1.0", "AGPL-3.0", "AGPL-3.0-only", "AGPL-3.0-or-later",
    "GPL-2.0", "GPL-3.0", "GPL-3.0-only", "GPL-3.0-or-later",
    "CC-BY-NC-4.0", "CC-BY-NC-SA-4.0", "CC-BY-ND-4.0",
    "FSL-1.1", "FSL-1.1-MIT", "FSL-1.1-Apache-2.0",
    "PolyForm-NC-1.0", "PolyForm-Noncommercial-1.0.0",
    "BUSL-1.1",
    "Proprietary",
})

YELLOW_LICENSES = frozenset({
    "LGPL-2.1", "LGPL-3.0", "LGPL-3.0-only",
    "CC-BY-SA-4.0", "CC-BY-4.0",
    "EPL-2.0",
})

_JUNK_LIC_STRINGS = frozenset({
    "", "Unknown", "unknown", "LICENSE", "LICENSE.txt", "LICENSE.md",
    "License", "License.txt", "License.md", "license", "license.txt",
})


def normalize_license(lic: str | None) -> str | None:
    """Normalize a free-form licence string to a canonical SPDX-style
    identifier, returning None for unparseable strings."""
    if not lic:
        return None
    s = lic.strip()
    if not s or s in _JUNK_LIC_STRINGS:
        return None
    sl = s.lower()
    if sl.startswith("complete terms in"):
        return None

    # MIT family
    if "mit-0" in sl:
        return "MIT-0"
    if re.match(r"^mit($|\s|\.|,|;)", sl) and "0" not in sl:
        return "MIT"
    if sl in ("mit", "mit license", "mit licence"):
        return "MIT"

    # Apache
    if sl in (
        "apache-2.0", "apache 2.0", "apache 2", "apache-2", "apache2",
        "apache-2.0 license", "apache 2.0 license", "apache license 2.0",
    ):
        return "Apache-2.0"
    if "apache" in sl and "2" in sl:
        return "Apache-2.0"

    # BSD / ISC / MPL / CC0 / Unlicense / WTFPL / Mulan
    if "bsd-3-clause" in sl or "3-clause bsd" in sl or "3 clause bsd" in sl:
        return "BSD-3-Clause"
    if "bsd-2-clause" in sl or "2-clause bsd" in sl:
        return "BSD-2-Clause"
    if sl == "0bsd":
        return "0BSD"
    if sl == "bsd":
        return "BSD"
    if sl == "isc":
        return "ISC"
    if "mpl-2.0" in sl or sl == "mpl":
        return "MPL-2.0"
    if "cc0" in sl or "cc-0" in sl:
        return "CC0-1.0"
    if "unlicense" in sl:
        return "Unlicense"
    if "wtfpl" in sl:
        return "WTFPL"
    if "mulan" in sl:
        return "Mulan-PSL-2.0"

    # Non-green
    if "agpl" in sl:
        return "AGPL-3.0"
    if "lgpl" in sl:
        return "LGPL-3.0"
    if "gpl" in sl:
        return "GPL-3.0"
    if "cc-by-nc" in sl or "cc by-nc" in sl:
        return "CC-BY-NC-4.0"
    if "cc-by-nd" in sl or "cc by-nd" in sl:
        return "CC-BY-ND-4.0"
    if "cc-by-sa" in sl or "cc by-sa" in sl:
        return "CC-BY-SA-4.0"
    if "cc-by" in sl or "cc by" in sl:
        return "CC-BY-4.0"
    if "proprietary" in sl or "private" in sl:
        return "Proprietary"
    if "fsl" in sl:
        return "FSL-1.1"
    if "polyform" in sl:
        return "PolyForm-NC-1.0"
    if "busl" in sl:
        return "BUSL-1.1"

    # Unknown — return None so caller falls back to source-level
    return None


def load_source_license_map(csv_path: str | Path) -> dict[str, str]:
    """Load source → license_category mapping from the enrichment CSV
    produced by `scripts/enrich_unmapped_licenses.py`."""
    out: dict[str, str] = {}
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            out[row["source"]] = row["license_category"]
    return out


def is_green_license(
    record_license: str | None,
    source: str,
    source_license_map: dict[str, str],
) -> bool:
    """Return True iff the skill is commercially redistributable.

    Resolution order:
      1. Normalise the per-skill licence string.  If it resolves to a
         GREEN identifier, return True.
      2. Otherwise fall back to the source repository's GitHub
         `spdx_id` from `source_license_map`.  If that is GREEN, return
         True.
      3. Otherwise return False (NO_LICENSE / fetch failed / RED /
         YELLOW / Custom / unparseable).

    Note that an explicit NON-GREEN per-skill string overrides any
    source-level GREEN inference, to honour the most restrictive
    declaration.  If the per-skill string is parseable AND non-GREEN,
    return False regardless of source-level licence.
    """
    norm = normalize_license(record_license)
    if norm is not None:
        if norm in GREEN_LICENSES:
            return True
        # Explicit non-green per-skill declaration: reject even if source-green
        return False
    # Per-skill string unparseable → fall back to source-level
    src_lic = source_license_map.get(source)
    return src_lic in GREEN_LICENSES if src_lic else False

# For maintaining the active status of skills by license in bulk, see ``license_audit.py``.
