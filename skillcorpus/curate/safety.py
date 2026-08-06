"""curate.safety — regex hard-block safety gate (pure, no LLM).

Stage-5 safety hard-gate, condition 1 of 3 (paper §3.3): a single pre-judge
``blocked.malware`` regex that rejects known-malicious skills at ingest, before
the LLM judge runs. Conditions 2-3 — firing any of the five LLM hard-gate flags,
or an LLM safety subscore < 3 — are applied post-judge by curate.safety_gate.

The former suspicious-* substring heuristics are NOT gates: a stratified audit
measured their false-positive rate above 90%, so they add no value as filters,
and the LLM judge's safety dimension plus its 19-flag vocabulary cover the rest.
Operators may register additional ``blocked.<name>`` patterns here; any
``blocked.*`` flag rejects.
"""
from __future__ import annotations

import re


_SAFETY_RULES: list[tuple[str, re.Pattern]] = [
    ("blocked.malware", re.compile(r"ClawdAuthenticatorTool", re.IGNORECASE)),
]


def check_safety(text: str) -> list[str]:
    """Return the names of triggered flags; an empty list means safe."""
    return [flag for flag, pat in _SAFETY_RULES if pat.search(text)]


def is_blocked(flags: list[str]) -> bool:
    """Any ``blocked.*`` flag means the skill is rejected from ingestion."""
    return any(f.startswith("blocked.") for f in flags)
