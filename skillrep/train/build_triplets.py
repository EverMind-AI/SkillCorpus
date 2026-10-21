#!/usr/bin/env python3
"""Step 3: assemble the training triplets from the mined requests.

Each request is paired with its skill as the positive and with its mined skills as negatives; every
skill is given whole (name, description and body). Requests whose skill is missing from the pool are
dropped.

Output: JSONL {"task", "query", "positive": {"name", "description", "body"}, "negatives": [...]},
read by train_encoder.py.

Example:
  python train/build_triplets.py --mined data/train_mined.jsonl --pool data/train_pool.jsonl \
      --out data/train_triplets.jsonl
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_jsonl, skill_id  # noqa: E402


def as_document(skill):
    return {"name": skill.get("name") or "", "description": skill.get("description") or "",
            "body": skill.get("body") or ""}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mined", required=True, help="output of mine_hard_negatives.py")
    ap.add_argument("--pool", required=True, help="training skill pool (JSONL)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    skills = {skill_id(s): s for s in load_jsonl(a.pool)}
    n_in = n_out = 0
    with open(a.out, "w", encoding="utf-8") as fout:
        for rec in load_jsonl(a.mined):
            n_in += 1
            pos = str(rec["positive"])
            if pos not in skills:
                continue
            negatives = [as_document(skills[str(n)]) for n in rec["negatives"] if str(n) in skills]
            fout.write(json.dumps({"task": rec["task"], "query": rec["query"],
                                   "positive": as_document(skills[pos]), "negatives": negatives},
                                  ensure_ascii=False) + "\n")
            n_out += 1
    print(f"{n_in} mined requests -> {n_out} triplets in {a.out}")


if __name__ == "__main__":
    main()
