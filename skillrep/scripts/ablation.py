#!/usr/bin/env python3
"""Ablation: each module added alone to the encoder and removed last from the full system,
on the fine-tuned and on the untuned encoder, with paired bootstrap intervals over requests.

The lexical channel searches with the hypothetical skills only when query-side expansion is on, so the
gain of query-side expansion includes the text it gives the lexical channel.

Example:
  python scripts/ablation.py --pool data/unipool/pool.jsonl --requests data/sra/requests.jsonl \
      --hypothetical data/sra/hypothetical_skills.json \
      --emb finetuned=emb/unipool_sra_ft --emb untuned=emb/unipool_sra_untuned --out results/ablation.json
Add --pairs to also report the interaction of every pair of modules.
"""
import argparse
import json
import os
import sys
from itertools import combinations

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from skillrep import SkillRep, SkillRepConfig
from skillrep.background import generic_words
from skillrep.data import lexical_text, load_embeddings, load_json, load_jsonl, macro_groups
from skillrep.lexical import BM25, lexical_queries
from skillrep.metrics import paired_bootstrap, reciprocal_ranks

MODULES = ["qe", "de", "bp", "lex"]
NAMES = {"qe": "Query-side expansion", "de": "Document-side expansion",
         "bp": "Background projection", "lex": "Lexical channel"}


def config(on):
    return SkillRepConfig(w=0.3 if "qe" in on else 0.0, u=0.2 if "de" in on else 0.0,
                          svd_rank=2 if "bp" in on else 0, generic_rank=2 if "bp" in on else 0,
                          lexical="lex" in on)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True)
    ap.add_argument("--requests", required=True)
    ap.add_argument("--hypothetical", required=True, help="hypothetical skills JSON (texts)")
    ap.add_argument("--emb", action="append", required=True, help="name=emb_dir, e.g. finetuned=emb/ft")
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--n_boot", type=int, default=20000)
    ap.add_argument("--pairs", action="store_true")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    pool, reqs = load_jsonl(a.pool), load_jsonl(a.requests)
    groups = macro_groups(reqs)
    members = [np.nonzero(np.asarray(groups) == g)[0] for g in sorted(set(groups))]
    macro = lambda rr: float(np.mean([rr[m].mean() for m in members]))
    ci = lambda x, y: paired_bootstrap(x, y, groups, a.n_boot)[1]

    texts = [lexical_text(s) for s in pool]
    generic = generic_words(texts)
    bm25 = BM25(texts)
    hyp_texts = load_json(a.hypothetical)
    lex_hits = {False: bm25.search(lexical_queries(reqs), 100),
                True: bm25.search(lexical_queries(reqs, hyp_texts), 100)}
    encoders = dict(x.split("=", 1) for x in a.emb)

    cache, embs = {}, {}

    def rr(enc, on):
        key = (enc, frozenset(on))
        if key not in cache:
            if enc not in embs:
                embs[enc] = load_embeddings(encoders[enc], reqs, pool)
            emb = embs[enc]
            cfg = config(on)
            sr = SkillRep(pool, emb["docs"], cfg, pseudo=emb.get("pseudo"), device=a.device, generic=generic)
            hits = lex_hits["qe" in on] if cfg.lexical else None
            rk = sr.search(reqs, emb["requests"], hypothetical=emb.get("hypothetical"), lexical_hits=hits)
            cache[key] = reciprocal_ranks(rk, reqs)
            print(f"  {enc:<10} {'+'.join(sorted(on)) or 'bare':<16} MRR@10 {macro(cache[key]):.2f}", flush=True)
        return cache[key]

    res = {}
    for enc in encoders:
        bare, full = rr(enc, []), rr(enc, MODULES)
        r = {"bare": macro(bare), "full": macro(full)}
        for m in MODULES:
            alone, drop = rr(enc, [m]), rr(enc, [x for x in MODULES if x != m])
            r[m] = {"alone": macro(alone) - r["bare"], "alone_ci": ci(alone, bare),
                    "last": r["full"] - macro(drop), "last_ci": ci(full, drop)}
        r["sum_alone"] = sum(r[m]["alone"] for m in MODULES)
        r["together"] = r["full"] - r["bare"]
        if a.pairs:
            r["pairs"] = {}
            for x, y in combinations(MODULES, 2):
                both, ox, oy = rr(enc, [x, y]), rr(enc, [x]), rr(enc, [y])
                r["pairs"][f"{x}+{y}"] = {"interaction": macro(both) - macro(ox) - macro(oy) + r["bare"],
                                          "ci": ci(both + bare, ox + oy)}
        res[enc] = r
    if "finetuned" in encoders and "untuned" in encoders:
        res["finetuning"] = {
            "alone": res["finetuned"]["bare"] - res["untuned"]["bare"],
            "alone_ci": ci(rr("finetuned", []), rr("untuned", [])),
            "last": res["finetuned"]["full"] - res["untuned"]["full"],
            "last_ci": ci(rr("finetuned", MODULES), rr("untuned", MODULES))}

    for enc in encoders:
        r = res[enc]
        print(f"\n[{enc}] bare {r['bare']:.2f}  full {r['full']:.2f}")
        for m in MODULES:
            print(f"  {NAMES[m]:<24} alone {r[m]['alone']:+6.2f}  last {r[m]['last']:+6.2f}")
        print(f"  together {r['together']:+.2f}  sum of alone {r['sum_alone']:+.2f}")
    if "finetuning" in res:
        print(f"\nEncoder fine-tuning  alone {res['finetuning']['alone']:+.2f}  last {res['finetuning']['last']:+.2f}")
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()
