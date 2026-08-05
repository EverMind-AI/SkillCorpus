"""curate.safety — regex hard-block safety gate (pure, no LLM)."""
from __future__ import annotations

import re


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
