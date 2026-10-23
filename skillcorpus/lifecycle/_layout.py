# SPDX-License-Identifier: MIT
"""On-disk layout and chat-completion helpers shared by the lifecycle tools.

A task directory holds ``task.md``, an optional ``inputs/`` folder and ``rubrics/task-completion.json`` plus
``rubrics/instruction-following.json``. Task ids have the form ``tessl-single-<skill>_<idx>``: the prefix names the
single-skill task format, and ``<skill>`` is the name of the skill directory under the skills root
(``skills/<skill>/SKILL.md`` plus bundled files).
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

TASK_PREFIX = "tessl-single-"

# extra_body that disables thinking on vLLM / SGLang servers of Qwen-style models
NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}}


def skill_of(task_id: str) -> str:
    """Skill directory name of a task id (``tessl-single-<skill>_<idx>`` -> ``<skill>``)."""
    return re.sub(r"_\d+$", "", re.sub(r"^" + re.escape(TASK_PREFIX), "", task_id))


def find_skill_md(skill_dir: Path) -> Path | None:
    """The SKILL.md of a skill directory: the top-level file if present, else the first one below it (the file name
    is matched case-insensitively)."""
    top = [f for f in skill_dir.iterdir() if f.is_file() and f.name.lower() == "skill.md"] if skill_dir.is_dir() else []
    if top:
        return sorted(top)[0]
    nested = [f for f in sorted(skill_dir.rglob("*")) if f.is_file() and f.name.lower() == "skill.md"]
    return nested[0] if nested else None


def skill_md(skill_dir: Path) -> str | None:
    """Text of the SKILL.md of a skill directory, or None when the directory has none."""
    f = find_skill_md(skill_dir)
    return f.read_text(encoding="utf-8", errors="ignore") if f else None


def reasoning_text(m) -> str:
    """The separate reasoning field of a chat-completion message (``reasoning_content`` or, on newer vLLM servers,
    ``reasoning``); empty when the server returns none."""
    return getattr(m, "reasoning_content", None) or getattr(m, "reasoning", None) or ""


def msg_text(m) -> str:
    """Reply text of a chat-completion message, including the reasoning field some servers return separately."""
    return (m.content or "") + reasoning_text(m)


def json_values(text: str, opener: str = "{") -> Iterator[Any]:
    """Every complete top-level JSON value that starts with ``opener`` in ``text``, left to right. Text around and
    between the values (prose, code fences, stray brackets) is skipped."""
    dec, i = json.JSONDecoder(), text.find(opener)
    while i != -1:
        try:
            value, end = dec.raw_decode(text, i)
        except json.JSONDecodeError:
            i = text.find(opener, i + 1)
            continue
        yield value
        i = text.find(opener, end)


def reply_json(m, accept: Callable[[Any], bool], opener: str = "{") -> Any:
    """The JSON answer of a chat-completion message: the first accepted value in the content, or, when the content
    has none, the last accepted value in the reasoning field some servers return separately."""
    for value in json_values(m.content or "", opener):
        if accept(value):
            return value
    found = [v for v in json_values(reasoning_text(m), opener) if accept(v)]
    if found:
        return found[-1]
    raise ValueError("no JSON answer in the reply")


def is_dict_with(*keys: str) -> Callable[[Any], bool]:
    return lambda v: isinstance(v, dict) and all(k in v for k in keys)
