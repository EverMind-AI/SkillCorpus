#!/usr/bin/env python3
"""Encode the two extra indexed views searched by `weights.py search`.

  whole.pt   {"emb": [N, d], "pids": [...]}     "name | description[:500] | body[:8000]", one vector per skill
  chunks.pt  {"emb": [M, d], "owner": [M]}      "name | description[:300] | chunk", owner = skill row index

Chunks follow the body's level-2/3 markdown headings (first 12,000 characters): pieces under 300 characters
are merged forward, pieces over 2,000 characters are cut into 1,200-character windows overlapping by 120,
and a skill keeps at most 8 chunks. A skill is scored by its best chunk.

Example:
  python analysis/weights_encode_views.py --model EverMind-AI/skillrep-embedding-0.6b --pool data/unipool/pool.jsonl \
      --out_dir emb/unipool_views
"""
import argparse
import os
import re
import sys

import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from skillrep.data import BODY_MAX, load_jsonl  # noqa: E402
from skillrep.encoder import Encoder  # noqa: E402

CHUNK_TARGET, CHUNK_MAX, CHUNK_MIN, CHUNK_OVERLAP, MAX_CHUNKS, CHUNK_BODY_MAX = 1200, 2000, 300, 120, 8, 12000


def text(v):
    if isinstance(v, str):
        return v
    if isinstance(v, (list, tuple)):
        return " ".join(str(x) for x in v)
    return "" if v is None else str(v)


def whole_text(s):
    return f"{text(s.get('name'))} | {text(s.get('description'))[:500]} | {text(s.get('body'))[:BODY_MAX]}"


def split_chunks(body):
    b = text(body)[:CHUNK_BODY_MAX].strip()
    if not b:
        return []
    merged = []
    for p in re.split(r"\n(?=#{2,3}\s)", b):
        p = p.strip()
        if not p:
            continue
        if merged and (len(p) < CHUNK_MIN or len(merged[-1]) < CHUNK_MIN):
            merged[-1] = merged[-1] + "\n" + p
        else:
            merged.append(p)
    out = []
    for p in merged:
        if len(p) <= CHUNK_MAX:
            out.append(p)
            continue
        for i in range(0, len(p), CHUNK_TARGET - CHUNK_OVERLAP):
            out.append(p[i:i + CHUNK_TARGET])
    return out[:MAX_CHUNKS] or [b[:CHUNK_MAX]]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--model", required=True)
    ap.add_argument("--pool", required=True)
    ap.add_argument("--out_dir", required=True)
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--batch_size", type=int, default=8)
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    pool = load_jsonl(a.pool)
    enc = Encoder(a.model, a.device)
    whole = enc([whole_text(s) for s in pool], a.batch_size)
    torch.save({"emb": whole, "pids": [s["pid"] for s in pool]}, os.path.join(a.out_dir, "whole.pt"))
    texts, owner = [], []
    for i, s in enumerate(pool):
        head = f"{text(s.get('name'))} | {text(s.get('description'))[:300]} | "
        for c in split_chunks(s.get("body")):
            texts.append(head + c)
            owner.append(i)
    chunks = enc(texts, 4 * a.batch_size, max_length=1024)
    torch.save({"emb": chunks, "owner": torch.tensor(owner, dtype=torch.long)}, os.path.join(a.out_dir, "chunks.pt"))
    print(f"whole {tuple(whole.shape)}, chunks {tuple(chunks.shape)}")


if __name__ == "__main__":
    main()
