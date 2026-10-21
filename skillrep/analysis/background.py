#!/usr/bin/env python3
"""The background gap (Section 6.1, Table 8, Section 7.1 and Appendix D).

Reproduces, for SRA-Bench requests on UniPool:
  Section 6.1  share of the library that scores above the gold document under <q, P_B d> alone
               (B_D on the untuned encoder, B_Q on the fine-tuned one); the angle between B_Q and the
               plane of B_D; the requests' component on B_Q; the B_Q component of gold and of other
               documents in the top ten.
  Table 8      background gap along B_D and B_Q for the untuned and the fine-tuned encoder, and with each
               module added to the fine-tuned encoder (bases fixed to that encoder's own space). Changes
               are reported as differences of the gaps rounded to two decimals, as in the table. The total
               score difference (competitors minus gold) is negative throughout, which is why selecting
               the top ten alone would push the gap negative.
  Section 7.1  mean norm of a request's component on B_D, each encoder in its own space; the controls for
               document-side expansion on the fine-tuned encoder: projecting B_Q out of the documents, and
               mixing the same library-wide mean pseudo-request into every document.
  Appendix D   the gap of Table 8 along the top k singular directions of the documents, k = 1..6.

Example:
  python analysis/background.py --pool data/unipool/pool.jsonl --requests data/sra/requests.jsonl \
      --hypothetical data/sra/hypothetical_skills.json \
      --emb_ft emb/unipool_sra_ft --emb_untuned emb/unipool_sra_untuned --out results/background.json
"""
import argparse
import json
import os

import numpy as np
import torch
import torch.nn.functional as F

from common import Embeddings, Setting, U, add_setting_args, background_projection, rankings, rr_from_rankings
from skillrep.measures import (background_gap, background_rank, document_background, request_background,
                               top_k)


def principal_angle(v, plane):
    """Angle in degrees between the unit vector(s) v [1, d] and the span of the orthonormal rows of plane."""
    s = torch.linalg.svdvals(v @ plane.T).clamp(max=1).max()
    return float(torch.rad2deg(torch.acos(s)))


def gaps(setting, q, d, bases, top):
    """Mean background gap (x 100) along each basis, and the mean total score difference (x 100)."""
    out = {name: float(np.nanmean(background_gap(q, d, b, top, setting.gold, setting.gold_first)))
           for name, b in bases.items()}
    tot = []
    for i, row in enumerate(top):
        comp = [j for j in row if j not in setting.gold[i]]
        if comp:
            tot.append(float((q[i] @ d[comp].T).mean() - q[i] @ d[setting.gold_first[i]]))
    out["total"] = float(np.mean(tot)) * 100
    return out


def mrr(setting, q, d):
    return setting.macro(rr_from_rankings(rankings(q, d), setting.gold)) * 100


def encoder_stats(setting, e, name):
    """Quantities of one encoder in its own space, with the bare dense ranking."""
    b_d = document_background(e.docs, 2)
    b_q = request_background(e.pseudo[0], e.queries)
    top = top_k(e.queries, e.docs, 10)
    r = {"gap": gaps(setting, e.queries, e.docs, {"B_D": b_d, "B_Q": b_q}, top)}
    r["above_gold_pct"] = {k: float(background_rank(e.queries, e.docs, b, setting.gold_first).mean())
                           for k, b in (("B_D", b_d), ("B_Q", b_q))}
    r["request_norm_on_B_D"] = float((e.queries @ b_d.T).norm(dim=1).mean())
    r["angle_B_Q_to_B_D"] = principal_angle(b_q, b_d)
    cq = (e.queries @ b_q[0]).cpu().numpy()
    r["request_component_on_B_Q"] = {"mean": float(cq.mean()), "share_positive": float((cq > 0).mean())}
    cd = (e.docs @ b_q[0]).cpu().numpy()
    gold_c, other_c = [], []
    for i, row in enumerate(top):
        for j in row:
            (gold_c if j in setting.gold[i] else other_c).append(cd[j])
    r["top10_component_on_B_Q"] = {"gold": float(np.mean(gold_c)), "others": float(np.mean(other_c))}
    v6 = document_background(e.docs, 6)
    r["gap_B_D_top_k"] = {k: float(np.nanmean(background_gap(e.queries, e.docs, v6[:k], top, setting.gold,
                                                             setting.gold_first))) for k in range(1, 7)}
    print(f"[{name}] gap B_D {r['gap']['B_D']:.2f}  B_Q {r['gap']['B_Q']:.2f}  total {r['gap']['total']:.2f}  "
          f"above gold: B_D {r['above_gold_pct']['B_D']:.1f}%  B_Q {r['above_gold_pct']['B_Q']:.1f}%  "
          f"|P_BD q| {r['request_norm_on_B_D']:.3f}", flush=True)
    return r, b_d, b_q


def module_rows(setting, e, b_d, b_q):
    """Table 8 rows: each module added alone to the fine-tuned encoder, bases fixed."""
    bases = {"B_D": b_d, "B_Q": b_q}
    d_bp, _, b_g = background_projection(e.docs, setting.generic)
    q_bp = e.queries - (e.queries @ b_g.T) @ b_g
    conds = {"+query-side exp.": (e.expand_queries(), e.docs, None),
             "+doc.-side exp.": (e.queries, e.expand_docs(), None),
             "+background proj.": (q_bp, d_bp, None),
             "+lexical channel": (e.queries, e.docs, setting.lexical_hits(True))}
    out = {}
    for name, (q, d, hits) in conds.items():
        top = rankings(q, d, hits)
        out[name] = gaps(setting, q, d, bases, top)
        print(f"  {name:<20} gap B_D {out[name]['B_D']:.2f}  B_Q {out[name]['B_Q']:.2f}", flush=True)
    return out


def de_controls(setting, e, b_q):
    """Section 7.1: document-side expansion against two controls (dense channel only, MRR@10 gains)."""
    bare = mrr(setting, e.queries, e.docs)
    owner = e.pseudo[1]
    sums = torch.zeros_like(e.docs).index_add_(0, owner, e.pseudo[0])
    counts = torch.zeros(e.docs.shape[0], device=e.device).index_add_(
        0, owner, torch.ones(len(owner), device=e.device))
    has = counts > 0
    own_mean = sums[has] / counts[has, None]
    const = F.normalize(own_mean.mean(0, keepdim=True), dim=-1)  # library-wide mean pseudo-request
    d_const = e.docs.clone()
    d_const[has] = F.normalize((1 - U) * e.docs[has] + U * const, dim=-1)
    d_proj = F.normalize(e.docs - (e.docs @ b_q.T) @ b_q, dim=-1)
    r = {"bare": bare, "doc_side_expansion": mrr(setting, e.queries, e.expand_docs()) - bare,
         "project_B_Q_out_of_docs": mrr(setting, e.queries, d_proj) - bare,
         "mix_library_mean_pseudo_request": mrr(setting, e.queries, d_const) - bare}
    print("  DE controls " + "  ".join(f"{k} {v:+.2f}" for k, v in r.items() if k != "bare"), flush=True)
    return r


def table6(res):
    """Rows of Table 8 as printed in the paper (changes are differences of rounded gaps)."""
    r2 = lambda x: round(x, 2)
    un, ft = res["untuned"]["gap"], res["fine-tuned"]["gap"]
    rows = {"Untuned encoder": [r2(un["B_D"]), r2(un["B_Q"])],
            "+ fine-tuning": [r2(r2(ft[k]) - r2(un[k])) for k in ("B_D", "B_Q")],
            "Fine-tuned encoder": [r2(ft["B_D"]), r2(ft["B_Q"])]}
    for name, g in res["modules"].items():
        rows[name.replace("+", "+ ")] = [r2(r2(g[k]) - r2(ft[k])) for k in ("B_D", "B_Q")]
    return rows


def main():
    ap = argparse.ArgumentParser()
    add_setting_args(ap, encoders=("ft", "untuned"))
    a = ap.parse_args()
    setting = Setting(a.pool, a.requests, a.hypothetical)
    res = {}
    e = Embeddings(setting, a.emb_untuned, a.device)
    res["untuned"], _, _ = encoder_stats(setting, e, "untuned")
    del e
    torch.cuda.empty_cache()
    e = Embeddings(setting, a.emb_ft, a.device)
    res["fine-tuned"], b_d, b_q = encoder_stats(setting, e, "fine-tuned")
    res["modules"] = module_rows(setting, e, b_d, b_q)
    res["doc_side_controls"] = de_controls(setting, e, b_q)
    res["table6"] = table6(res)
    print("\nTable 8 (x 100)          B_D     B_Q")
    for k, (x, y) in res["table6"].items():
        print(f"  {k:<22}{x:>7.2f}{y:>8.2f}")
    k_gaps = {n: list(res[n]["gap_B_D_top_k"].values()) for n in ("untuned", "fine-tuned")}
    print("Appendix D, gap along the top k directions (k = 1..6): " +
          "  ".join(f"{n} {min(v):.2f}-{max(v):.2f}" for n, v in k_gaps.items()))
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()
