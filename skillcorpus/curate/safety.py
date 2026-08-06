"""curate.safety — regex hard-block safety gate (pure, no LLM)."""
from __future__ import annotations

import re


# ======================== safety regex hard-block ========================
"""Safety check — regex hard-block with an empty default rule set.

The default ships **no** patterns: context-free substring blocklists proved
unusable (their false-positive rate measured near 98%), so the safety gate is
the LLM judge instead — its `safety` dimension plus anti-signal `flags`,
enforced in quality.py:
  - numeric hard-gate: LLM safety < 3 → quality = 0
  - flag hard-gate:    prompt_injection / cmd_injection / unsafe_exec /
                       auth_bypass / csam_risk → quality = 0

This module stays as an extension hook: an operator can register their own
``blocked.<name>`` patterns to force-reject known-bad content at ingest, and
any ``blocked.*`` flag rejects.
"""



_SAFETY_RULES: list[tuple[str, re.Pattern]] = []


def check_safety(text: str) -> list[str]:
    """Return the names of triggered flags; an empty list means safe."""
    return [flag for flag, pat in _SAFETY_RULES if pat.search(text)]


def is_blocked(flags: list[str]) -> bool:
    """Any ``blocked.*`` flag means the skill is rejected from ingestion."""
    return any(f.startswith("blocked.") for f in flags)
