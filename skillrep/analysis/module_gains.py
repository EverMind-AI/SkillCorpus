#!/usr/bin/env python3
"""Per-request gain of each module added alone (the rows of Table 7).

Each module is added to the fine-tuned encoder on its own, and fine-tuning to the untuned encoder;
the gain is the change in the request's RR@10 (in [0, 1]). The expansions act on the dense channel
only, background projection rescales the documents after both steps, and the lexical channel fuses
BM25 with the request alone by score (alpha = 0.3).

Example:
  python analysis/module_gains.py --pool data/unipool/pool.jsonl --requests data/sra/requests.jsonl \
      --emb_ft emb/unipool_sra_ft --emb_untuned emb/unipool_sra_untuned \
      --npz results/module_gains.npz --out results/module_gains.json
"""
import argparse
import json

import numpy as np
import torch

from common import Embeddings, Setting, add_setting_args, background_projection, rr

MODULES = ["finetuning", "query_expansion", "doc_expansion", "background_projection", "lexical_channel"]


def main():
    ap = argparse.ArgumentParser()
    add_setting_args(ap, encoders=("ft", "untuned"))
    ap.add_argument("--npz", required=True, help="output .npz with <module>_gain and <module>_base per request")
    a = ap.parse_args()

    s = Setting(a.pool, a.requests)
    un = Embeddings(s, a.emb_untuned, a.device)
    untuned = rr(s, un.queries, un.docs)
    del un
    torch.cuda.empty_cache()

    ft = Embeddings(s, a.emb_ft, a.device)
    base = rr(s, ft.queries, ft.docs)
    cond = {
        "finetuning": base,
        "query_expansion": rr(s, ft.expand_queries(), ft.docs),
        "doc_expansion": rr(s, ft.queries, ft.expand_docs()),
        "background_projection": rr(s, ft.queries, background_projection(ft.docs, s.generic, rescale_step2=True)[0]),
        "lexical_channel": rr(s, ft.queries, ft.docs, hits=s.lexical_hits(False)),
    }
    bases = {m: (untuned if m == "finetuning" else base) for m in MODULES}

    arrays, summary = {}, {}
    for m in MODULES:
        arrays[f"{m}_gain"] = cond[m] - bases[m]
        arrays[f"{m}_base"] = bases[m]
        summary[m] = {"mrr_base": s.macro(bases[m]) * 100, "mrr_with": s.macro(cond[m]) * 100,
                      "gain": s.macro(cond[m] - bases[m]) * 100}
        print(f"{m:<22} MRR@10 {summary[m]['mrr_base']:.2f} -> {summary[m]['mrr_with']:.2f} "
              f"({summary[m]['gain']:+.2f})", flush=True)
    np.savez(a.npz, **arrays)
    with open(a.out, "w") as f:
        json.dump(summary, f, indent=1)


if __name__ == "__main__":
    main()
