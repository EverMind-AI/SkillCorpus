#!/usr/bin/env python3
"""Run SkillRep (or any subset of its modules) on one pool and one request set (Tables 2-4).

Example, full system on SRA-Bench with UniPool:
  python scripts/run_skillrep.py --pool data/unipool/pool.jsonl --requests data/sra/requests.jsonl \
      --emb_dir emb/unipool_sra_ft --hypothetical data/sra/hypothetical_skills.json --out results/unipool_full.json
Without query-side expansion (the "w/o QE" rows): add --w 0.
"""
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from skillrep import SkillRep, SkillRepConfig, evaluate
from skillrep.data import load_embeddings, load_json, load_jsonl, macro_groups
from skillrep.metrics import reciprocal_ranks


def add_config_args(ap):
    d = SkillRepConfig()
    ap.add_argument("--w", type=float, default=d.w, help="query-side expansion weight (0 = off)")
    ap.add_argument("--u", type=float, default=d.u, help="document-side expansion weight (0 = off)")
    ap.add_argument("--svd_rank", type=int, default=d.svd_rank, help="background projection step 1 (0 = off)")
    ap.add_argument("--generic_rank", type=int, default=d.generic_rank, help="background projection step 2 (0 = off)")
    ap.add_argument("--no_lexical", action="store_true", help="turn the lexical channel off")
    ap.add_argument("--lexical_hypothetical", default=d.lexical_hypothetical, choices=["auto", "always", "never"])
    ap.add_argument("--alpha", type=float, default=d.alpha)
    ap.add_argument("--fusion", default=d.fusion, choices=["score", "rank"])


def config_from_args(a):
    return SkillRepConfig(w=a.w, u=a.u, svd_rank=a.svd_rank, generic_rank=a.generic_rank,
                          lexical=not a.no_lexical, lexical_hypothetical=a.lexical_hypothetical,
                          alpha=a.alpha, fusion=a.fusion)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True)
    ap.add_argument("--requests", required=True)
    ap.add_argument("--emb_dir", required=True, help="output directory of scripts/encode.py")
    ap.add_argument("--hypothetical", help="hypothetical skills JSON (texts, for the lexical channel)")
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--out", help="metrics JSON")
    ap.add_argument("--save_rankings", help="JSONL with the top-50 pids of each request")
    ap.add_argument("--save_rr", help=".npy with the per-request reciprocal rank (x 100)")
    add_config_args(ap)
    a = ap.parse_args()

    pool, reqs = load_jsonl(a.pool), load_jsonl(a.requests)
    emb = load_embeddings(a.emb_dir, reqs, pool)
    cfg = config_from_args(a)
    sr = SkillRep(pool, emb["docs"], cfg, pseudo=emb.get("pseudo"), device=a.device)
    hyp_texts = load_json(a.hypothetical) if a.hypothetical else None
    rankings = sr.search(reqs, emb["requests"], hypothetical=emb.get("hypothetical"), hypothetical_texts=hyp_texts)
    res = evaluate(rankings, reqs, macro_groups(reqs))
    print(json.dumps({"config": vars(cfg), **res}))
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w") as f:
            json.dump({"config": vars(cfg), "metrics": res}, f, indent=1)
    if a.save_rankings:
        with open(a.save_rankings, "w") as f:
            for r, rk in zip(reqs, rankings):
                f.write(json.dumps({"task": r["task"], "ranked_pids": rk}) + "\n")
    if a.save_rr:
        np.save(a.save_rr, reciprocal_ranks(rankings, reqs))


if __name__ == "__main__":
    main()
