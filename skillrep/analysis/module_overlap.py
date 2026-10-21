#!/usr/bin/env python3
"""Why the modules overlap (Section 7.4, Table 10).

Reproduces, for SRA-Bench requests on UniPool, dense channel only, on the untuned and the fine-tuned encoder:
  Table 10     all eight combinations of query-side expansion (QE), document-side expansion (DE) and
               background projection (BP), with MRR@10 and the background gaps of Table 6 (bases estimated
               once, in each encoder's bare space). Reported: how much DE and BP narrow the B_Q gap and what
               they gain, alone and after QE, and QE's own narrowing (differences of gaps rounded to two
               decimals, as in the table); the pairwise interactions with 95% paired bootstrap intervals; and
               the overlap of the three modules, their gains alone summed minus their gain together.
  Section 7.4  QE's gain at w = 0.3 after projecting backgrounds out first: none, the two-level projection
               (BD), B_Q projected out of the documents (BQ), or both (BD+BQ); and the specificity of B_Q:
               its cosine with the mean document, request and hypothetical skill, and their mean components
               along it.

Example:
  python analysis/module_overlap.py --pool data/unipool/pool.jsonl --requests data/sra/requests.jsonl \
      --emb_ft emb/unipool_sra_ft --emb_untuned emb/unipool_sra_untuned --out results/module_overlap.json
"""
import argparse
import itertools
import json
import os

import numpy as np
import torch
import torch.nn.functional as F

from common import Embeddings, Setting, W, add_setting_args, background_projection, rr_from_rankings
from skillrep.measures import background_gap, document_background, request_background, top_k
from skillrep.metrics import paired_bootstrap

MODULES = ["QE", "DE", "BP"]
BACKGROUNDS = ["none", "BD", "BQ", "BD+BQ"]


def build(setting, e, on, w=W):
    """Request and document embeddings with the given modules applied, as in the full pipeline."""
    q = e.expand_queries(w=w) if "QE" in on and w > 0 else e.queries
    d = e.expand_docs() if "DE" in on else e.docs
    if "BP" in on:
        d, _, b_g = background_projection(d, setting.generic)
        q = q - (q @ b_g.T) @ b_g
    return q, d


def evaluate(setting, q, d, b_d, b_q):
    """Per-request RR@10 (x 100) and background gaps (x 100) of the dense ranking."""
    top = top_k(q, d, 10)
    return (rr_from_rankings(top, setting.gold) * 100,
            background_gap(q, d, b_d, top, setting.gold, setting.gold_first),
            background_gap(q, d, b_q, top, setting.gold, setting.gold_first))


def combinations(setting, e, b_d, b_q, name, n_boot):
    runs = {}
    for c in (c for n in range(4) for c in itertools.combinations(MODULES, n)):
        rr, g_d, g_q = evaluate(setting, *build(setting, e, set(c)), b_d, b_q)
        runs[frozenset(c)] = {"rr": rr, "gap_BD": float(np.nanmean(g_d)), "gap_BQ": float(np.nanmean(g_q))}
        print(f"[{name}] {'+'.join(c) or 'bare':<10} MRR@10 {setting.macro(rr):6.2f}  "
              f"gap B_D {runs[frozenset(c)]['gap_BD']:6.2f}  gap B_Q {runs[frozenset(c)]['gap_BQ']:6.2f}", flush=True)
    m = lambda s: setting.macro(runs[frozenset(s)]["rr"])
    gq = lambda s: round(runs[frozenset(s)]["gap_BQ"], 2)
    r = {"conditions": {"+".join(sorted(c, key=MODULES.index)) or "bare":
                        {"mrr": setting.macro(v["rr"]), "gap_BD": v["gap_BD"], "gap_BQ": v["gap_BQ"]}
                        for c, v in runs.items()}}
    r["table10"] = {
        "B_Q gap narrowed by QE": round(gq([]) - gq(["QE"]), 2),
        **{f"B_Q gap narrowed by {x}, alone": round(gq([]) - gq([x]), 2) for x in ("DE", "BP")},
        **{f"B_Q gap narrowed by {x}, after QE": round(gq(["QE"]) - gq(["QE", x]), 2) for x in ("DE", "BP")},
        **{f"MRR@10 gain of {x}, alone": m([x]) - m([]) for x in ("DE", "BP")},
        **{f"MRR@10 gain of {x}, after QE": m(["QE", x]) - m(["QE"]) for x in ("DE", "BP")}}
    r["interaction"] = {}
    for a, b in itertools.combinations(MODULES, 2):
        x = (runs[frozenset([a, b])]["rr"] - runs[frozenset([a])]["rr"] - runs[frozenset([b])]["rr"]
             + runs[frozenset()]["rr"])
        delta, ci = paired_bootstrap(x, np.zeros_like(x), setting.groups, n_boot)
        r["interaction"][f"{a}x{b}"] = {"delta": delta, "ci": ci}
    sum_alone = sum(m([x]) - m([]) for x in MODULES)
    r["three"] = {"sum_alone": sum_alone, "together": m(MODULES) - m([]),
                  "overlap": sum_alone - (m(MODULES) - m([]))}
    return r


def qe_after_background(setting, e, b_q, weights, n_boot):
    """QE's gain under each background treatment (Section 7.4)."""
    d_bd, _, b_g = background_projection(e.docs, setting.generic)
    proj_q = lambda d: F.normalize(d - (d @ b_q.T) @ b_q, dim=-1)
    rr, mrr = {}, {}
    for bg in BACKGROUNDS:
        d = {"none": e.docs, "BD": d_bd, "BQ": proj_q(e.docs), "BD+BQ": proj_q(d_bd)}[bg]
        for w in weights:
            q = e.expand_queries(w=w) if w > 0 else e.queries
            if "BD" in bg:
                q = q - (q @ b_g.T) @ b_g
            rr[(bg, w)] = rr_from_rankings(top_k(q, d, 10), setting.gold) * 100
            mrr[f"{bg}|w{w}"] = setting.macro(rr[(bg, w)])
    r = {"mrr": mrr, "qe_gain_at_w0.3": {bg: setting.macro(rr[(bg, W)] - rr[(bg, 0)]) for bg in BACKGROUNDS}}
    base = rr[("none", W)] - rr[("none", 0)]
    r["change_vs_none"] = {}
    for bg in BACKGROUNDS[1:]:
        delta, ci = paired_bootstrap(rr[(bg, W)] - rr[(bg, 0)], base, setting.groups, n_boot)
        r["change_vs_none"][bg] = {"delta": delta, "ci": ci}
    print("  QE gain at w=0.3 " + "  ".join(f"{k} {v:+.2f}" for k, v in r["qe_gain_at_w0.3"].items()), flush=True)
    return r


def b_q_specificity(e, b_q):
    out = {}
    for k, x in (("docs", e.docs), ("requests", e.queries), ("hypothetical", e.hypothetical[0])):
        mean = x.mean(0)
        out[k] = {"mean_norm": float(mean.norm()), "cos_B_Q_mean": float(F.normalize(mean, dim=0) @ b_q[0]),
                  "mean_component_on_B_Q": float((x @ b_q[0]).mean())}
    return out


def main():
    ap = argparse.ArgumentParser()
    add_setting_args(ap, encoders=("ft", "untuned"))
    ap.add_argument("--weights", default="0,0.3", help="QE weights for the background sweep (0 must be included)")
    ap.add_argument("--n_boot", type=int, default=5000)
    a = ap.parse_args()
    weights = sorted({float(x) for x in a.weights.split(",")} | {0.0, W})
    setting = Setting(a.pool, a.requests)
    res = {}
    for name, path in (("untuned", a.emb_untuned), ("fine-tuned", a.emb_ft)):
        e = Embeddings(setting, path, a.device)
        b_d = document_background(e.docs, 2)
        b_q = request_background(e.pseudo[0], e.queries)
        r = combinations(setting, e, b_d, b_q, name, a.n_boot)
        r["qe_after_background"] = qe_after_background(setting, e, b_q, weights, a.n_boot)
        r["b_q_specificity"] = b_q_specificity(e, b_q)
        res[name] = r
        del e
        torch.cuda.empty_cache()
    print("\nTable 10                                   untuned  fine-tuned")
    for k in res["untuned"]["table10"]:
        print(f"  {k:<40}{res['untuned']['table10'][k]:>8.2f}{res['fine-tuned']['table10'][k]:>11.2f}")
    for name in res:
        t = res[name]["three"]
        print(f"[{name}] three modules: sum alone {t['sum_alone']:.2f}, together {t['together']:.2f}, "
              f"overlap {t['overlap']:.2f}; cos(B_Q, mean document) "
              f"{res[name]['b_q_specificity']['docs']['cos_B_Q_mean']:.2f}")
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()
