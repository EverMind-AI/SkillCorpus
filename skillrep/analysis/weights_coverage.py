#!/usr/bin/env python3
"""Appendix C: how much of their skill the requests of a benchmark already state.

Mean and median coverage (Section 6.2) on the three counts, lexical, semantic (cosine >= 0.8) and soft,
with each word embedded on its own by the untuned encoder. Requests with no content words, or whose
gold body has none, are skipped. Used to explain why query-side expansion helps less on SkillRet.

Example:
  python analysis/weights_coverage.py --model Qwen/Qwen3-Embedding-0.6B \
      --benchmark sra data/unipool/pool.jsonl data/sra/requests.jsonl \
      --benchmark skillret data/skillret/pool.jsonl data/skillret/requests.jsonl --out results/coverage.json
"""
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from skillrep.data import load_jsonl  # noqa: E402
from skillrep.encoder import Encoder  # noqa: E402
from skillrep.measures import content_words, gold_body, lexical_coverage, semantic_coverage, word_embeddings  # noqa: E402


def coverage(pool_path, requests_path, encoder, tau=0.8):
    pool = {s["pid"]: s for s in load_jsonl(pool_path)}
    reqs = load_jsonl(requests_path)
    qw = [sorted(set(content_words(r["query"]))) for r in reqs]
    gw = [sorted(set(content_words(gold_body(pool[r["gt_pids"][0]])))) for r in reqs]
    vocab = sorted(set(w for ws in qw + gw for w in ws))
    word_id = {w: i for i, w in enumerate(vocab)}
    emb = word_embeddings(encoder, vocab).to(encoder.device)
    lex, sem, soft = [], [], []
    for q, g in zip(qw, gw):
        if not q or not g:
            continue
        s, f = semantic_coverage(q, g, emb, word_id, taus=(tau,))
        lex.append(lexical_coverage(q, g))
        sem.append(s[tau])
        soft.append(f)
    return {k: {"mean": float(np.mean(v)), "median": float(np.median(v)), "n": len(v)}
            for k, v in (("lexical", lex), ("semantic", sem), ("soft", soft))}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--model", required=True, help="untuned Qwen3-Embedding-0.6B")
    ap.add_argument("--benchmark", nargs=3, action="append", required=True, metavar=("NAME", "POOL", "REQUESTS"))
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    enc = Encoder(a.model, a.device)
    res = {}
    for name, pool, reqs in a.benchmark:
        res[name] = coverage(pool, reqs, enc)
        print(name, json.dumps(res[name]), flush=True)
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()
