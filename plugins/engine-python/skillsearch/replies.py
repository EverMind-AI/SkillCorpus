"""Pulling a JSON object out of a model's reply.

Both model calls in this package ask for "only JSON" and both get told
otherwise: a fenced block, a reasoning preamble, a sentence of commentary
after the closing fence. This is one extractor rather than two because the
two calls previously disagreed about how much of that to tolerate — the
gate handled all of it, the rewriter handled only a fence that ended the
string — and the difference was invisible until a live model appended a
sentence and the rewriter's verdict was silently discarded.
"""

from __future__ import annotations

import json
import re
from typing import Any

_THINK = re.compile(r"<think>[\s\S]*?</think>")
_FENCED = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)
_BRACED = re.compile(r"\{.*\}", re.DOTALL)


def extract_json_object(content: str) -> dict[str, Any] | None:
    """Find the one JSON object in a model reply.

    Tolerates, in order: a reasoning block, a fenced block anywhere in the
    reply — including one followed by commentary — and a bare object
    embedded in prose.

    Args:
        content: the model's reply, verbatim.

    Returns:
        The decoded object, or ``None`` when the reply carries no JSON
        object at all. Callers decide what a missing object means; it is
        never an exception here, because for both of them it is a normal
        model behaviour with a defined fallback.
    """
    text = _THINK.sub("", content or "").strip()
    if not text:
        return None

    fenced = _FENCED.search(text)
    candidates = [fenced.group(1).strip()] if fenced else []
    braced = _BRACED.search(text)
    if braced:
        candidates.append(braced.group(0))
    candidates.append(text)

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


__all__ = ["extract_json_object"]
