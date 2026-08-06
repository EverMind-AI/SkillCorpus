"""Load GDPval tasks (rubric + occupation + reference-file list) so the grader
has something to score against.

Grading only needs a task's ``rubric_json``, ``occupation`` and the basenames of
its ``reference_files`` (inputs handed to the agent, excluded from the graded
deliverable set). These come from the public ``openai/gdpval`` HuggingFace
dataset — this module fetches them by ``task_id`` (full 32-char id or the
8-char short id) — or you can hand-write a task dict / load one from JSON and
skip HuggingFace entirely.
"""

import functools
import json
from typing import Dict, List, Optional

# Fields we keep from each dataset row. Everything the grader reads plus a few
# for human context; skill / deliverable-download machinery is intentionally out.
_KEEP = ("task_id", "occupation", "sector", "prompt",
         "reference_files", "reference_file_urls")


def _normalize(row: dict) -> Dict:
    """Map one raw ``openai/gdpval`` row to a grader task dict."""
    rubric = row.get("rubric_json")
    if not isinstance(rubric, str):
        rubric = json.dumps(rubric, ensure_ascii=False)
    task = {k: row.get(k) for k in _KEEP}
    task["rubric_json"] = rubric
    task["rubric_pretty"] = row.get("rubric_pretty", "")
    task["reference_files"] = task.get("reference_files") or []
    return task


@functools.lru_cache(maxsize=1)
def _load_dataset(owner: str = "openai", name: str = "gdpval",
                  split: str = "train") -> tuple:
    """Load and cache the full dataset as a tuple of normalized task dicts."""
    from datasets import load_dataset  # imported lazily: only needed for HF path
    ds = load_dataset(f"{owner}/{name}", split=split)
    return tuple(_normalize(dict(r)) for r in ds)


def load_gdpval_tasks(owner: str = "openai", name: str = "gdpval",
                      split: str = "train") -> List[Dict]:
    """Return all GDPval tasks as grader task dicts (list order = dataset order)."""
    return list(_load_dataset(owner, name, split))


def load_gdpval_task(task_id: str, owner: str = "openai", name: str = "gdpval",
                     split: str = "train") -> Dict:
    """Return one task by full or 8-char short ``task_id``. Raises KeyError if
    not found, ValueError if a short id is ambiguous."""
    matches = [t for t in _load_dataset(owner, name, split)
               if t["task_id"] == task_id or t["task_id"][:8] == task_id]
    if not matches:
        raise KeyError(f"No GDPval task with id {task_id!r}")
    if len(matches) > 1:
        raise ValueError(
            f"Short id {task_id!r} is ambiguous ({len(matches)} matches); "
            "use the full task_id")
    return matches[0]


def load_task_from_json(path: str) -> Dict:
    """Load a hand-written / exported task dict from a JSON file (offline path).

    The file must contain at least ``occupation`` and ``rubric_json``. If
    ``rubric_json`` is a list it is normalized to a JSON string.
    """
    with open(path, encoding="utf-8") as f:
        task = json.load(f)
    if "occupation" not in task or "rubric_json" not in task:
        raise ValueError(f"{path}: task must have 'occupation' and 'rubric_json'")
    if not isinstance(task["rubric_json"], str):
        task["rubric_json"] = json.dumps(task["rubric_json"], ensure_ascii=False)
    task.setdefault("reference_files", [])
    return task
