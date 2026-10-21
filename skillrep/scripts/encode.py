#!/usr/bin/env python3
"""Encode skill bodies, requests, hypothetical skills and pseudo-requests.

Outputs (torch .pt files in --out_dir):
  docs.pt          {"emb": [N, d], "pids": [...]}           skill bodies, encoded as documents
  requests.pt      {"emb": [M, d], "tasks": [...]}          requests, with the instruction
  hypothetical.pt  {"emb": [K, d], "owner": LongTensor[K]}  hypothetical skills, as documents
  pseudo.pt        {"emb": [K, d], "owner": LongTensor[K]}  pseudo-requests, with the instruction

Example:
  python scripts/encode.py --model EverMind-AI/skillrep-embedding-0.6b --pool data/unipool/pool.jsonl \
      --requests data/sra/requests.jsonl --hypothetical data/sra/hypothetical_skills.json \
      --pseudo data/unipool/pseudo_requests.json --out_dir emb/unipool_sra_ft
"""
import argparse
import os
import sys

import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from skillrep.data import BODY_MAX, INSTRUCTIONS, body_text, load_json, load_jsonl, pseudo_request_text, query_text
from skillrep.encoder import PSEUDO_MAX_LENGTH, Encoder


def flatten(texts_by_key, keys):
    texts, owner = [], []
    for i, k in enumerate(keys):
        for t in texts_by_key.get(k, []):
            if t and t.strip():
                texts.append(t)
                owner.append(i)
    return texts, torch.tensor(owner, dtype=torch.long)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--out_dir", required=True)
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--batch_size", type=int, default=8)
    ap.add_argument("--instruction", choices=sorted(INSTRUCTIONS), default="finetuned",
                    help="instruction prefix: 'finetuned' for our encoder, 'untuned' for Qwen3-Embedding-0.6B")
    ap.add_argument("--pool")
    ap.add_argument("--requests")
    ap.add_argument("--hypothetical")
    ap.add_argument("--pseudo")
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    enc = Encoder(a.model, a.device)
    instruction = INSTRUCTIONS[a.instruction]

    def save(name, obj):
        torch.save(obj, os.path.join(a.out_dir, name))
        print(f"wrote {name}: {tuple(obj['emb'].shape)}", flush=True)

    if a.pool:
        pool = load_jsonl(a.pool)
        save("docs.pt", {"emb": enc([body_text(s) for s in pool], a.batch_size), "pids": [s["pid"] for s in pool]})
        if a.pseudo:
            texts, owner = flatten(load_json(a.pseudo), [str(s["pid"]) for s in pool])
            emb = enc([pseudo_request_text(t, instruction) for t in texts], 4 * a.batch_size, PSEUDO_MAX_LENGTH)
            save("pseudo.pt", {"emb": emb, "owner": owner})
    if a.requests:
        reqs = load_jsonl(a.requests)
        save("requests.pt", {"emb": enc([query_text(r, instruction) for r in reqs], a.batch_size), "tasks": [r["task"] for r in reqs]})
        if a.hypothetical:
            texts, owner = flatten(load_json(a.hypothetical), [r["task"] for r in reqs])
            save("hypothetical.pt", {"emb": enc([t[:BODY_MAX] for t in texts], a.batch_size), "owner": owner})


if __name__ == "__main__":
    main()
