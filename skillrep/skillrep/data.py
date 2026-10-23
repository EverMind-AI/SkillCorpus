"""Loading skill pools, requests and generated texts, and the text views used for encoding and BM25.

File formats
------------
pool.jsonl      one skill per line: {"pid": int, "name": str, "description": str, "body": str}
requests.jsonl  one request per line: {"task": str, "query": str, "gt_pids": [int, ...], "dataset": str}
hypothetical skills (query-side expansion): JSON {task: [text, ...]}
pseudo-requests   (document-side expansion): JSON {pid (as str): [text, ...]}
"""
import json
import os

import torch

BODY_MAX = 8000  # characters of the skill body that are encoded and indexed

# Instruction prefixes for requests and pseudo-requests at retrieval time, each followed by one space. The paper's
# results use the first with the fine-tuned encoder (fine-tuning used the same sentence without the word "coding",
# see train/), and the second with the untuned encoder.
QUERY_INSTRUCTION = ("Instruct: Given a coding task description, retrieve the most relevant "
                     "skill document that would help an agent complete the task\nQuery:")
UNTUNED_INSTRUCTION = ("Instruct: Given a task description, retrieve the skill document that "
                       "helps an agent complete the task\nQuery:")
INSTRUCTIONS = {"finetuned": QUERY_INSTRUCTION, "untuned": UNTUNED_INSTRUCTION}


def load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def as_str(value):
    """Fields are occasionally lists or missing; encode them exactly as str() would."""
    if isinstance(value, str):
        return value
    return "" if value is None else str(value)


def body_text(skill, body_max=BODY_MAX):
    """The dense channel indexes the skill body only, without name and description."""
    body = as_str(skill.get("body"))
    return body if not body_max else body[:body_max]


def lexical_text(skill, body_max=BODY_MAX):
    """The lexical channel indexes name, description and body."""
    return f"{as_str(skill.get('name'))} {as_str(skill.get('description'))} {body_text(skill, body_max)}"


def query_text(request, instruction=QUERY_INSTRUCTION):
    """Requests are encoded with the instruction prefix and one space, and are never truncated by characters."""
    return instruction.rstrip() + " " + request["query"]


def pseudo_request_text(text, instruction=QUERY_INSTRUCTION):
    """Pseudo-requests are encoded like requests, with the instruction prefix followed by one space."""
    return instruction.rstrip() + " " + text.strip()


def macro_groups(requests):
    """Requests are grouped by source dataset for macro averages; a benchmark without the field is one group."""
    return [r.get("dataset") or r.get("task_type") or "all" for r in requests]


def load_embeddings(emb_dir, requests=None, pool=None):
    """Loads the files written by scripts/encode.py. Returns a dict with "docs", "requests" and, when
    present, "hypothetical" and "pseudo" as (emb, owner) pairs. Row order is checked against the inputs."""
    def get(name):
        path = os.path.join(emb_dir, name)
        return torch.load(path, weights_only=False) if os.path.exists(path) else None

    out = {}
    docs, reqs = get("docs.pt"), get("requests.pt")
    if docs is not None:
        if pool is not None:
            assert list(docs["pids"]) == [s["pid"] for s in pool], "docs.pt does not match the pool"
        out["docs"] = docs["emb"].float()
    if reqs is not None:
        if requests is not None:
            assert list(reqs["tasks"]) == [r["task"] for r in requests], "requests.pt does not match the requests"
        out["requests"] = reqs["emb"].float()
    for name in ("hypothetical", "pseudo"):
        z = get(f"{name}.pt")
        if z is not None:
            out[name] = (z["emb"].float(), z["owner"])
    return out
