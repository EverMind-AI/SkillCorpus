#!/usr/bin/env python3
"""Gating modules per request.

The full system (fine-tuned encoder, w = 0.3, u = 0.2, two-level background projection, score fusion
with alpha = 0.3) is compared with variants that run one module on only part of the requests.

Query-side expansion. A request without it uses neither hypothetical skills in the dense channel nor in
the lexical channel (BM25 searches with the request alone). Gates, each opening the half of requests
with the lowest value:
  probe top-1 coverage  share of the content words of the probe's top document that the request contains
  probe flatness        top-1 minus top-10 dense score of the probe
  oracle coverage       the same coverage computed on the gold document
The probe is one dense pass with the raw request against the full system's documents.

Lexical channel. A request without it is ranked by the dense channel alone. Gates:
  probe top-10 entropy  entropy of softmax(20 x top-10 dense scores); the most uncertain half
  probe top-1 score     the half with the lowest top-1 dense score
  oracle                requests whose gold document is not ranked first by the dense channel
(the dense scores here are the full system's, with query-side expansion).

Reported per gate: share of requests the module runs on, macro MRR@10 change against running it on
every request, and a 95% bootstrap interval that resamples gold-skill clusters within source datasets.

Example:
  python analysis/gating.py --pool data/unipool/pool.jsonl --requests data/sra/requests.jsonl \
      --hypothetical data/sra/hypothetical_skills.json --emb_ft emb/unipool_sra_ft --out results/gating.json
"""
import argparse
import json

import numpy as np
import torch
import torch.nn.functional as F

from common import ALPHA, Embeddings, Setting, add_setting_args, background_projection, dense_topn
from skillrep.background import project_out
from skillrep.fusion import score_fusion
from skillrep.measures import content_words, gold_body


def fused_rr(d_ids, d_sc, gold, hits=None, alpha=ALPHA):
    """Per-request RR@10 in [0, 1] from the dense top-100, fused with BM25 hits when given."""
    out = np.zeros(len(d_ids))
    for i in range(len(d_ids)):
        order = score_fusion(d_ids[i], d_sc[i], hits[0][i] if hits else None, hits[1][i] if hits else None, alpha)
        for r, j in enumerate(order[:10]):
            if j in gold[i]:
                out[i] = 1.0 / (r + 1)
                break
    return out


def cluster_bootstrap_ci(a, b, setting, n=20000, seed=0):
    """95% interval of the macro mean of a - b (x 100): within each source dataset, clusters of requests
    sharing a gold set are resampled and the dataset mean is the ratio of the resampled sums."""
    d = a - b
    cl = {}
    for i, r in enumerate(setting.reqs):
        cl.setdefault(setting.groups[i], {}).setdefault(tuple(sorted(r["gt_pids"])), []).append(i)
    subs = [[np.array(v) for v in c.values()] for c in cl.values()]
    sums = [np.array([d[x].sum() for x in c]) for c in subs]
    sizes = [np.array([len(x) for x in c]) for c in subs]
    rng = np.random.default_rng(seed)
    out = np.zeros(n)
    for t in range(n):
        m = []
        for S, Z in zip(sums, sizes):
            k = rng.integers(0, len(S), len(S))
            m.append(S[k].sum() / Z[k].sum())
        out[t] = np.mean(m) * 100
    return [float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))]


def main():
    ap = argparse.ArgumentParser()
    add_setting_args(ap)
    ap.add_argument("--n_boot", type=int, default=20000)
    a = ap.parse_args()
    s = Setting(a.pool, a.requests, a.hypothetical)
    e = Embeddings(s, a.emb_ft, a.device)

    docs, _, b_g = background_projection(e.expand_docs(), s.generic)
    q_exp = project_out(e.expand_queries(), b_g, rescale=False)
    q_raw = project_out(e.queries, b_g, rescale=False)
    dx_ids, dx_sc = dense_topn(q_exp, docs)  # full system
    dr_ids, dr_sc = dense_topn(q_raw, docs)  # probe: raw request
    hits_exp, hits_raw = s.lexical_hits(True), s.lexical_hits(False)

    rr_full = fused_rr(dx_ids, dx_sc, s.gold, hits_exp)
    res = {"full_system": s.macro(rr_full) * 100, "gates": {}}
    print(f"full system {res['full_system']:.2f}", flush=True)

    def report(name, on, rr, rr_ref):
        row = {"applied": float(on.mean() * 100), "gated": s.macro(rr) * 100, "uniform": s.macro(rr_ref) * 100,
               "delta": s.macro(rr - rr_ref) * 100, "delta_ci": cluster_bootstrap_ci(rr, rr_ref, s, a.n_boot)}
        res["gates"][name] = row
        print(name, json.dumps(row), flush=True)

    # query-side expansion
    body_words = {}

    def bw(j):
        if j not in body_words:
            body_words[j] = set(content_words(gold_body(s.pool[j])))
        return body_words[j]

    req_words = [set(content_words(r["query"])) for r in s.reqs]
    cov_probe = np.array([len(req_words[i] & bw(int(dr_ids[i][0]))) / max(1, len(bw(int(dr_ids[i][0]))))
                          for i in range(len(s.reqs))])
    cov_oracle = np.array([len(req_words[i] & bw(s.gold_first[i])) / max(1, len(bw(s.gold_first[i])))
                           for i in range(len(s.reqs))])
    flat = np.array([float(dr_sc[i][0] - dr_sc[i][9]) for i in range(len(s.reqs))])
    rr_off = fused_rr(dr_ids, dr_sc, s.gold, hits_raw)  # no LLM text for this request
    for name, score in (("qe_probe_top1_coverage", -cov_probe), ("qe_probe_flatness", -flat),
                        ("qe_oracle_coverage", -cov_oracle)):
        on = score >= np.median(score)  # lowest coverage / flattest half
        report(name, on, np.where(on, rr_full, rr_off), rr_full)

    # lexical channel
    top10 = torch.tensor(dx_sc[:, :10])
    p = F.softmax(top10 * 20, dim=1)
    entropy = (-(p * p.clamp_min(1e-12).log()).sum(1)).numpy()
    top1 = dx_sc[:, 0].astype(np.float64)
    pct = np.zeros(len(s.reqs))  # share of the library (%) scored above the gold document
    for k in range(0, len(s.reqs), 256):
        sc = q_exp[k:k + 256] @ docs.T
        g = torch.as_tensor(s.gold_first[k:k + 256], device=sc.device)
        pct[k:k + 256] = ((sc > sc.gather(1, g[:, None])).float().mean(1) * 100).cpu().numpy()
    rr_dense = fused_rr(dx_ids, dx_sc, s.gold)
    for name, on in (("lex_probe_top10_entropy", entropy >= np.median(entropy)),
                     ("lex_probe_top1_score", -top1 >= np.median(-top1)),
                     ("lex_oracle_gold_not_first", pct > 0)):
        report(name, on, np.where(on, rr_full, rr_dense), rr_full)

    with open(a.out, "w") as f:
        json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()
