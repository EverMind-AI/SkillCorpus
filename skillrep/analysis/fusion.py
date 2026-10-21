#!/usr/bin/env python3
"""Score fusion against rank fusion.

  margin   The lexical channel under rank fusion. Requests are split into fifths by the gold document's dense margin over
           its strongest competitor on the fine-tuned encoder alone (gold score minus the best other
           score). Per fifth: the mean margin, the share of the library (%) scored above the gold
           document, and the change in RR@10 (x 100) that the lexical channel makes when it is fused
           by rank (RRF, k = 60, alpha = 0.3) with SkillRep's dense channel.
  compare  The full system with score fusion and with rank fusion (R@10, MRR@10, Hit@1),
           and what the lexical channel adds last under each. Run once per pool.

Examples:
  python analysis/fusion.py margin --pool data/unipool/pool.jsonl --requests data/sra/requests.jsonl \
      --hypothetical data/sra/hypothetical_skills.json --emb_ft emb/unipool_sra_ft --out results/fusion_margin.json
  python analysis/fusion.py compare --pool data/sra/pool.jsonl --requests data/sra/requests.jsonl \
      --hypothetical data/sra/hypothetical_skills.json --emb_ft emb/sra_ft --out results/fusion_sra.json
"""
import argparse
import json

import numpy as np
import torch

from common import (ALPHA, TOPN, Embeddings, Setting, add_setting_args, background_projection, dense_topn,
                    rr_from_rankings)
from skillrep import SkillRep, SkillRepConfig, evaluate
from skillrep.fusion import RRF_K, rank_fusion
from skillrep.metrics import reciprocal_ranks


@torch.no_grad()
def margin_and_percentile(q, docs, gold_first, batch=128):
    """Gold score minus the best other score, and the share of the library (%) scored above the gold."""
    n = docs.shape[0]
    margin, pct = np.zeros(q.shape[0]), np.zeros(q.shape[0])
    for s in range(0, q.shape[0], batch):
        sc = q[s:s + batch] @ docs.T
        g = torch.as_tensor(gold_first[s:s + batch], device=sc.device)
        cg = sc.gather(1, g[:, None]).squeeze(1)
        pct[s:s + batch] = ((sc > cg[:, None]).sum(1).float() / n * 100).cpu().numpy()
        top2 = sc.topk(2, dim=1).values
        other = torch.where(top2[:, 0] > cg, top2[:, 0], top2[:, 1])
        margin[s:s + batch] = (cg - other).cpu().numpy()
    return margin, pct


def lexical_gain_rank_fusion(setting, q, docs, hits, alpha=ALPHA, k=RRF_K):
    """Per-request change in RR@10 from adding the lexical channel by rank fusion."""
    d_ids, _ = dense_topn(q, docs, TOPN)
    with_lex = [rank_fusion(d_ids[i], hits[0][i], alpha, k)[:10] for i in range(len(d_ids))]
    without = [list(map(int, r[:10])) for r in d_ids]
    return rr_from_rankings(with_lex, setting.gold) - rr_from_rankings(without, setting.gold)


def fifths(x):
    edges = np.quantile(x, np.linspace(0, 1, 6))
    return [(x >= edges[b]) & ((x <= edges[b + 1]) if b == 4 else (x < edges[b + 1])) for b in range(5)]


def run_margin(a):
    s = Setting(a.pool, a.requests, a.hypothetical)
    e = Embeddings(s, a.emb_ft, a.device)
    margin, pct = margin_and_percentile(e.queries, e.docs, s.gold_first)
    docs, _, _ = background_projection(e.expand_docs(), s.generic)
    gain = lexical_gain_rank_fusion(s, e.expand_queries(), docs, s.lexical_hits(True)) * 100
    rows = [dict(n=int(m.sum()), margin=float(margin[m].mean()), gold_percentile=float(pct[m].mean()),
                 lexical_gain=float(gain[m].mean())) for m in fifths(margin)]
    res = {"mean_lexical_gain": float(gain.mean()), "corr_margin_gain": float(np.corrcoef(margin, gain)[0, 1]),
           "fifths": rows}
    print(f"{'fifth':>5}{'margin':>10}{'gold pct':>10}{'dRR':>9}")
    for b, r in enumerate(rows):
        print(f"{b + 1:>5}{r['margin']:>+10.3f}{r['gold_percentile']:>10.3f}{r['lexical_gain']:>+9.2f}")
    return res


def run_compare(a):
    s = Setting(a.pool, a.requests, a.hypothetical)
    e = Embeddings(s, a.emb_ft, a.device)
    hits = s.lexical_hits(True)
    res, rr = {}, {}
    for name, cfg in (("score", SkillRepConfig(fusion="score")), ("rank", SkillRepConfig(fusion="rank")),
                      ("dense_only", SkillRepConfig(lexical=False))):
        sr = SkillRep(s.pool, e.docs, cfg, pseudo=e.pseudo, device=a.device, generic=s.generic)
        rk = sr.search(s.reqs, e.queries, hypothetical=e.hypothetical,
                       lexical_hits=hits if cfg.lexical else None)
        res[name] = evaluate(rk, s.reqs, s.groups)
        rr[name] = reciprocal_ranks(rk, s.reqs)
    for f in ("score", "rank"):   # what the lexical channel adds last, from unrounded macro averages
        res[f"lexical_last_{f}"] = round(s.macro(rr[f]) - s.macro(rr["dense_only"]), 2)
    for k, v in res.items():
        print(k, v)
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("margin", "compare"):
        add_setting_args(sub.add_parser(name))
    a = ap.parse_args()
    res = run_margin(a) if a.cmd == "margin" else run_compare(a)
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()
