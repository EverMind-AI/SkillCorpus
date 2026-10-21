#!/usr/bin/env python3
"""Appendix C: the expansion weights and their interactions.

  grid    Tables "wu" and "wutransfer". The (w, u) grid in the full system, with w and u weighting the
          dense channel only; the lexical channel searches with the hypothetical skills at every w. Reports
          each row's best u and its gain over u = 0, and how far tuning each weight alone (w at u = 0,
          u at w = 0) and combining the winners falls below the best joint setting. Run once per pool.
  search  The dev/test check of how much choosing the settings on the test requests is worth. Requests are
          split in half at random (seed 0), stratified by source dataset; the full grid of indexed view x
          u x projection x w x fusion (3 x 5 x 3 x 7 x 10 = 3,150 settings) is searched on one half and its
          winner is tested on the other. Here the lexical channel uses the hypothetical skills only when
          w > 0. The "whole" and "chunk" views need the extra embeddings written by weights_encode_views.py;
          without them only the body view is searched.

Examples:
  python analysis/weights.py grid --pool data/unipool/pool.jsonl --requests data/sra/requests.jsonl \
      --hypothetical data/sra/hypothetical_skills.json --emb_ft emb/unipool_sra_ft --out results/wu_unipool.json
  python analysis/weights.py search --pool data/unipool/pool.jsonl --requests data/sra/requests.jsonl \
      --hypothetical data/sra/hypothetical_skills.json --emb_ft emb/unipool_sra_ft \
      --views emb/unipool_views --out results/joint_search.json
"""
import argparse
import dataclasses
import itertools
import json
import os

import numpy as np
import torch
import torch.nn.functional as F

from common import Embeddings, Setting, add_setting_args, dense_topn, rr_from_rankings
from skillrep import SkillRep, SkillRepConfig, evaluate
from skillrep.background import generic_basis, project_out, svd_directions
from skillrep.fusion import rank_fusion, score_fusion

W_GRID, U_GRID = (0.0, 0.3, 0.5), (0.0, 0.1, 0.2, 0.3, 0.5)


# ---------------------------------------------------------------- (w, u) grid

def run_grid(a):
    s = Setting(a.pool, a.requests, a.hypothetical)
    e = Embeddings(s, a.emb_ft, a.device)
    hits = s.lexical_hits(True)
    ws = [float(x) for x in a.w_values.split(",")]
    us = [float(x) for x in a.u_values.split(",")]
    grid = {}
    for u in us:
        base = SkillRepConfig(u=u, lexical_hypothetical="always")
        sr = SkillRep(s.pool, e.docs, base, pseudo=e.pseudo, device=a.device, generic=s.generic)
        for w in ws:
            sr.cfg = dataclasses.replace(base, w=w)      # documents depend on u only
            rk = sr.search(s.reqs, e.queries, hypothetical=e.hypothetical, lexical_hits=hits)
            grid[(w, u)] = evaluate(rk, s.reqs, s.groups)
            print(f"w={w:g} u={u:g} {grid[(w, u)]}", flush=True)
    mrr = {k: v["MRR@10"] for k, v in grid.items()}
    rows = {}
    for w in ws:
        u_best = max(us, key=lambda u: mrr[(w, u)])
        rows[f"{w:g}"] = {"mrr": [mrr[(w, u)] for u in us], "u_best": u_best,
                          "gain": round(mrr[(w, u_best)] - mrr[(w, 0.0)], 2) if 0.0 in us else None}
    best = max(mrr, key=mrr.get)
    res = {"w": ws, "u": us, "rows": rows, "metrics": {f"w{w:g}_u{u:g}": v for (w, u), v in grid.items()},
           "best": {"w": best[0], "u": best[1], "mrr": mrr[best]}}
    if 0.0 in us and 0.0 in ws:
        w_alone = max(ws, key=lambda w: mrr[(w, 0.0)])
        u_alone = max(us, key=lambda u: mrr[(0.0, u)])
        res["tuned_separately"] = {"w": w_alone, "u": u_alone, "mrr": mrr[(w_alone, u_alone)],
                                   "below_best": round(mrr[best] - mrr[(w_alone, u_alone)], 2)}
    print(f"{'w':>5}" + "".join(f"{u:>8g}" for u in us) + f"{'u*':>6}{'gain':>8}")
    for w in ws:
        r = rows[f"{w:g}"]
        print(f"{w:>5g}" + "".join(f"{x:>8.2f}" for x in r["mrr"]) + f"{r['u_best']:>6g}{r['gain']:>+8.2f}")
    if "tuned_separately" in res:
        print("tuned separately:", res["tuned_separately"])
    return res


# ---------------------------------------------------------------- dev/test joint search

VIEWS = ["whole", "body", "chunk"]
US = [0, 0.1, 0.2, 0.3, 0.5]
PROJS = ["none", "svd2", "two"]
WS = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.7]
FUSES = ["none"] + [f"rrf{x}" for x in (0.3, 0.5, 1.0)] + [f"score{x}" for x in (0.1, 0.2, 0.3, 0.5, 0.7, 1.0)]
CHOSEN = dict(view="body", u=0.2, proj="two", w=0.3, fuse="score0.3")


def key(view, u, proj, w, fuse):
    return f"{view}|u{u}|{proj}|w{w}|{fuse}"


def dev_test_split(groups, seed=0):
    """0 = dev, 1 = test: each source dataset is shuffled and its second half goes to test."""
    groups = np.asarray(groups)
    rng = np.random.default_rng(seed)
    split = np.zeros(len(groups), dtype=int)
    for g in sorted(set(groups)):
        ix = rng.permutation(np.nonzero(groups == g)[0])
        split[ix[len(ix) // 2:]] = 1
    return split


def pseudo_means(e, n):
    """Per document, the mean of its pseudo-requests, and whether it has any."""
    emb, owner = e.pseudo
    sums = torch.zeros(n, emb.shape[1], device=emb.device).index_add_(0, owner, emb)
    counts = torch.zeros(n, device=emb.device).index_add_(0, owner, torch.ones(len(owner), device=emb.device))
    has = counts > 0
    sums[has] /= counts[has, None]
    return sums, has


def doc_matrix(x, owner, n, u, proj, means, generic):
    """One indexed view after document-side expansion and the chosen projection. For chunks, each chunk
    is mixed with its document's pseudo-requests and step 2 is estimated on the documents' summed chunks."""
    if u > 0:
        m, has = means if owner is None else (means[0][owner], means[1][owner])
        x = torch.where(has[:, None], F.normalize((1 - u) * x + u * m, dim=-1), x)
    if proj in ("svd2", "two"):
        x = project_out(x, svd_directions(x, 2), rescale=True)
    if proj == "two":
        d = x if owner is None else F.normalize(torch.zeros(n, x.shape[1], device=x.device).index_add_(0, owner, x), dim=-1)
        x = project_out(x, generic_basis(d, generic[0], generic[1], 2), rescale=False)
    return x


@torch.no_grad()
def dense_top_view(q, x, owner, n, k=100, batch=128):
    if owner is None:
        return dense_topn(q, x, k)
    ids, scores = [], []
    for s in range(0, q.shape[0], batch):
        sim = q[s:s + batch] @ x.T
        agg = torch.full((sim.shape[0], n), -1e4, device=sim.device)
        agg.scatter_reduce_(1, owner.unsqueeze(0).expand(sim.shape[0], -1), sim, reduce="amax")
        v, j = agg.topk(k, dim=1)
        ids.append(j.cpu().numpy())
        scores.append(v.float().cpu().numpy())
    return np.concatenate(ids), np.concatenate(scores)


def fused_rr(setting, d_ids, d_sc, hits, fuse):
    if fuse == "none":
        order = [list(map(int, r[:10])) for r in d_ids]
    elif fuse.startswith("rrf"):
        order = [rank_fusion(d_ids[i], hits[0][i], float(fuse[3:]))[:10] for i in range(len(d_ids))]
    else:
        order = [score_fusion(d_ids[i], d_sc[i], hits[0][i], hits[1][i], float(fuse[5:]))[:10]
                 for i in range(len(d_ids))]
    return rr_from_rankings(order, setting.gold)


def joint_search(setting, e, views, out_jsonl, device, view_names=None):
    """views: {"body": docs [N, d], "whole": docs [N, d], "chunk": (chunks [M, d], owner [M])}.
    Appends {key, dev, test} per setting to out_jsonl and resumes from it."""
    n = len(setting.pool)
    split = dev_test_split(setting.groups)
    grp = np.asarray(setting.groups)

    def macro(rr, half):
        return float(np.mean([rr[(split == half) & (grp == g)].mean() for g in sorted(set(setting.groups))]) * 100)

    done = {}
    if os.path.exists(out_jsonl):
        with open(out_jsonl) as f:
            for line in f:
                r = json.loads(line)
                done[r["key"]] = r
    means = pseudo_means(e, n)
    queries = {w: e.expand_queries(w=w) if w > 0 else e.queries for w in WS}
    view_names = view_names or [v for v in VIEWS if v in views]
    for view, u, proj in itertools.product(view_names, US, PROJS):
        if all(key(view, u, proj, w, f) in done for w in WS for f in FUSES):
            continue
        x, owner = views[view] if view == "chunk" else (views[view], None)
        owner = None if owner is None else owner.to(device)
        x = doc_matrix(x.to(device), owner, n, u, proj, means, setting.generic)
        for w in WS:
            if all(key(view, u, proj, w, f) in done for f in FUSES):
                continue
            d_ids, d_sc = dense_top_view(queries[w], x, owner, n)
            hits = setting.lexical_hits(w > 0)
            with open(out_jsonl, "a") as fo:
                for f in FUSES:
                    k = key(view, u, proj, w, f)
                    if k in done:
                        continue
                    rr = fused_rr(setting, d_ids, d_sc, hits, f)
                    done[k] = {"key": k, "dev": macro(rr, 0), "test": macro(rr, 1)}
                    fo.write(json.dumps(done[k]) + "\n")
            print(f"{view} u{u} {proj} w{w}: best dev {max(done[key(view, u, proj, w, f)]['dev'] for f in FUSES):.2f}",
                  flush=True)
        del x
        torch.cuda.empty_cache()
    return done


def summarize(done):
    """The dev winner and its test score, against the best test score of any setting."""
    joint = max(done.values(), key=lambda r: r["dev"])
    oracle = max(done.values(), key=lambda r: r["test"])
    chosen = done.get(key(**CHOSEN))
    res = {"n_settings": len(done), "dev_winner": joint, "best_on_test": oracle,
           "winner_below_best_on_test": oracle["test"] - joint["test"], "chosen": chosen}
    print(json.dumps(res, indent=1))
    return res


def load_views(path, setting, docs):
    views = {"body": docs}
    if path:
        w = os.path.join(path, "whole.pt")
        c = os.path.join(path, "chunks.pt")
        if os.path.exists(w):
            z = torch.load(w, weights_only=False)
            assert list(z["pids"]) == [s["pid"] for s in setting.pool]
            views["whole"] = F.normalize(z["emb"].float(), dim=-1)
        if os.path.exists(c):
            z = torch.load(c, weights_only=False)
            views["chunk"] = (F.normalize(z["emb"].float(), dim=-1), z["owner"].long())
    return views


def run_search(a):
    s = Setting(a.pool, a.requests, a.hypothetical)
    e = Embeddings(s, a.emb_ft, a.device)
    views = load_views(a.views, s, e.docs)
    done = joint_search(s, e, views, a.out + "l", a.device)
    return summarize(done)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("grid")
    add_setting_args(g)
    g.add_argument("--w_values", default=",".join(map(str, W_GRID)))
    g.add_argument("--u_values", default=",".join(map(str, U_GRID)))
    sp = sub.add_parser("search")
    add_setting_args(sp)
    sp.add_argument("--views", help="directory with whole.pt and chunks.pt from weights_encode_views.py")
    a = ap.parse_args()
    res = run_grid(a) if a.cmd == "grid" else run_search(a)
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()
