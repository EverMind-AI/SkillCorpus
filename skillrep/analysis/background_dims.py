#!/usr/bin/env python3
"""Background projection: controls and dimensionality (Appendix D).

1. Table "bgdims": the MRR@10 change from projecting directions out of the documents
     fine_tuned  the fine-tuned encoder alone (dense channel): random directions (mean of ten seeds),
                 the top-k singular vectors (k = 1, 2, 3, 4, 6, 8; projected out and rescaled), the two
                 generic-word directions alone, and the two-level projection
     full        the full system with its projection replaced: step 1 with k directions and no step 2,
                 step 2 alone, and the two-level projection, against the full system without one
     untuned     the untuned encoder alone: top-k singular vectors and the two-level projection
2. The background gap along the top-k singular vectors (k = 1, 2, 3, 4, 6) on both encoders alone.
3. Rescaling after step 2: MRR@10 of the full system and of the projection alone when the documents
   are rescaled after the generic-word directions are subtracted, and the documents' mean squared
   length along those directions after step 1.

Example:
  python analysis/background_dims.py --pool data/unipool/pool.jsonl --requests data/sra/requests.jsonl \
      --hypothetical data/sra/hypothetical_skills.json --emb_ft emb/unipool_sra_ft \
      --emb_untuned emb/unipool_sra_untuned --out results/background_dims.json
"""
import argparse
import json

import numpy as np
import torch
import torch.nn.functional as F

from common import Embeddings, Setting, add_setting_args, rankings, rr_from_rankings
from skillrep import SkillRep, SkillRepConfig
from skillrep.background import generic_basis, project_out, svd_directions
from skillrep.measures import background_gap, top_k
from skillrep.metrics import paired_bootstrap, reciprocal_ranks

SVD_KS = (1, 2, 3, 4, 6, 8)
GAP_KS = (1, 2, 3, 4, 6)
FULL_CONFIGS = [(k, 0) for k in (0, 1, 2, 3, 4, 6, 8)] + [(0, 2), (2, 2)]  # (step-1 rank, step-2 rank)


def orth(v):
    return torch.linalg.qr(v.T)[0].T


def dense_rr(setting, q, docs):
    return rr_from_rankings(top_k(q, docs, 10), setting.gold)


def alone_curve(setting, e, generic, random=False):
    """MRR@10 changes (x 100) on one encoder alone, dense channel only."""
    q, d = e.queries, e.docs
    bare = dense_rr(setting, q, d)
    out = {"bare": setting.macro(bare) * 100}
    delta = lambda docs, queries=q: setting.macro(dense_rr(setting, queries, docs) - bare) * 100
    for k in SVD_KS:
        out[f"svd{k}"] = delta(project_out(d, svd_directions(d, k), rescale=True))
    if random:
        for k in (2, 4):
            vals = []
            for seed in range(10):
                g = torch.Generator(device="cpu").manual_seed(seed)
                v = orth(torch.randn(k, d.shape[1], generator=g).to(d.device))
                vals.append(delta(project_out(d, v, rescale=True)))
            out[f"random{k}"] = {"mean": float(np.mean(vals)), "min": float(np.min(vals)), "max": float(np.max(vals))}
        out["generic2"] = delta(project_out(d, generic_basis(d, generic[0], generic[1], 2), rescale=True))
    x = project_out(d, svd_directions(d, 2), rescale=True)
    b_g = generic_basis(x, generic[0], generic[1], 2)
    out["two_level"] = delta(project_out(x, b_g, rescale=False), project_out(q, b_g, rescale=False))
    return out


def full_column(setting, e, pool_emb):
    """Full system with step-1 rank k and step-2 rank r; MRR@10 (x 100) and change from (0, 0)."""
    hits = setting.lexical_hits(True)
    out = {}
    for k, r in FULL_CONFIGS:
        cfg = SkillRepConfig(svd_rank=k, generic_rank=r)
        sr = SkillRep(setting.pool, pool_emb["docs"], cfg, pseudo=pool_emb["pseudo"], device=e.device,
                      generic=setting.generic)
        rk = sr.search(setting.reqs, pool_emb["queries"], hypothetical=pool_emb["hypothetical"], lexical_hits=hits)
        out[f"svd{k}_generic{r}"] = setting.macro(reciprocal_ranks(rk, setting.reqs))
        del sr
        torch.cuda.empty_cache()
    base = out["svd0_generic0"]
    return {"mrr": out, "delta": {key: v - base for key, v in out.items()}}


def gap_by_k(setting, e):
    top = top_k(e.queries, e.docs, 10)
    s8 = svd_directions(e.docs, 8)
    return {f"svd_top{k}": float(np.nanmean(background_gap(e.queries, e.docs, s8[:k], top, setting.gold,
                                                             setting.gold_first)))
            for k in GAP_KS}


def step2_rescale(setting, e, generic):
    """Full system and projection alone, with step 2 subtracted only (as in SkillRep) or then rescaled."""
    hits = setting.lexical_hits(True)
    res = {}
    for name, full in (("full", True), ("projection_only", False)):
        docs = e.expand_docs() if full else e.docs
        q = e.expand_queries() if full else e.queries
        x = project_out(docs, svd_directions(docs, 2), rescale=True)
        b_g = generic_basis(x, generic[0], generic[1], 2)
        sq_len = ((x @ b_g.T) ** 2).sum(1)
        x = project_out(x, b_g, rescale=False)
        q = project_out(q, b_g, rescale=False)
        rr = {}
        for variant, d in (("subtract_only", x), ("rescaled", F.normalize(x, dim=-1))):
            rr[variant] = rr_from_rankings(rankings(q, d, hits if full else None), setting.gold) * 100
        diff, ci = paired_bootstrap(rr["rescaled"], rr["subtract_only"], setting.groups, n=5000)
        res[name] = {"subtract_only": setting.macro(rr["subtract_only"]), "rescaled": setting.macro(rr["rescaled"]),
                     "diff": diff, "diff_ci": ci, "step2_sq_len_mean": float(sq_len.mean()),
                     "step2_sq_len_p90": float(torch.quantile(sq_len.float(), 0.9))}
    return res


def main():
    ap = argparse.ArgumentParser()
    add_setting_args(ap, encoders=("ft", "untuned"))
    a = ap.parse_args()
    s = Setting(a.pool, a.requests, a.hypothetical)
    generic = s.generic
    res = {}

    ft = Embeddings(s, a.emb_ft, a.device)
    res["fine_tuned"] = alone_curve(s, ft, generic, random=True)
    print("fine-tuned alone", json.dumps(res["fine_tuned"]), flush=True)
    res["gap_by_k"] = {"fine_tuned": gap_by_k(s, ft)}
    res["step2_rescale"] = step2_rescale(s, ft, generic)
    print("step-2 rescale", json.dumps(res["step2_rescale"]), flush=True)
    raw = {"docs": ft.docs, "queries": ft.queries, "pseudo": ft.pseudo, "hypothetical": ft.hypothetical}
    res["full"] = full_column(s, ft, raw)
    print("full system", json.dumps(res["full"]["delta"]), flush=True)
    del ft, raw
    torch.cuda.empty_cache()

    un = Embeddings(s, a.emb_untuned, a.device)
    res["untuned"] = alone_curve(s, un, generic)
    res["gap_by_k"]["untuned"] = gap_by_k(s, un)
    print("untuned alone", json.dumps(res["untuned"]), flush=True)
    print("gap by k", json.dumps(res["gap_by_k"]), flush=True)
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()
