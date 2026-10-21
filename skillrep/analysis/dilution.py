#!/usr/bin/env python3
"""Figure 3: which retriever dilution hurts (Section 7.3).

Three retrievers search separately, without fusion:
  one vector  the fine-tuned encoder, one vector per skill body
  sections    the same encoder indexing each section separately (encode_sections.py --mode index),
              a skill scored by its best section
  BM25        the request alone (name, description and body; dilution_utils.bm25_top)
Each request's RR@10 is related to its gold document's dilution loss l(q) = cos(q, s*) - cos(q, d)
(Eq. 2; sections from encode_sections.py --mode gold): RR by fifths of l (micro averages) and the
partial correlation of RR with l controlling lexical coverage, log body words and log number of
indexed sections, with a 95% interval over resampled gold clusters.

Example:
  python analysis/dilution.py --pool data/unipool/pool.jsonl --requests data/sra/requests.jsonl \
      --emb_ft emb/unipool_sra_ft --sections emb/unipool_sra_ft/sections.pt \
      --gold_sections emb/unipool_sra_ft/gold_sections.pt --out results/dilution.json
"""
import argparse
import json
import os

import numpy as np
import torch
import torch.nn.functional as F

from common import Setting  # also puts the package root on sys.path
from dilution_utils import best_section_scores, bm25_doc_text, bm25_top, load_section_index, rr_from_top
from skillrep.data import load_embeddings
from skillrep.measures import (cluster_bootstrap, content_words, dilution_loss, gold_body, gold_clusters,
                               lexical_coverage, partial_corr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True)
    ap.add_argument("--requests", required=True)
    ap.add_argument("--emb_ft", required=True, help="scripts/encode.py output for the fine-tuned encoder")
    ap.add_argument("--sections", required=True, help="encode_sections.py --mode index output")
    ap.add_argument("--gold_sections", required=True, help="encode_sections.py --mode gold output")
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--n_boot", type=int, default=2000)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    st = Setting(a.pool, a.requests)
    emb = load_embeddings(a.emb_ft, st.reqs, st.pool)
    docs = F.normalize(emb["docs"], dim=-1).to(a.device)
    queries = F.normalize(emb["requests"], dim=-1).to(a.device)
    sec_emb, sec_owner = load_section_index(a.sections, a.device)
    n_docs = docs.shape[0]

    # top ten of each retriever
    top = {"one_vector": [], "sections": []}
    with torch.no_grad():
        for s in range(0, queries.shape[0], 64):
            q = queries[s:s + 64]
            top["one_vector"] += (q @ docs.T).topk(10, dim=1).indices.cpu().tolist()
            top["sections"] += best_section_scores(q, sec_emb, sec_owner, docs).topk(10, dim=1).indices.cpu().tolist()
    top["bm25"] = bm25_top([bm25_doc_text(s) for s in st.pool], [r["query"] for r in st.reqs], n=10)
    rr = {k: rr_from_top(v, st.gold) for k, v in top.items()}

    # dilution loss and the controls
    gs = torch.load(a.gold_sections, weights_only=False)
    loss = dilution_loss(queries.cpu(), docs.cpu(), F.normalize(gs["emb"].float(), dim=-1), gs["owner"].numpy(),
                         st.gold_first)
    cov = np.array([lexical_coverage(content_words(r["query"]), content_words(gold_body(st.pool[g])))
                    for r, g in zip(st.reqs, st.gold_first)])
    logw = np.log1p([len(gold_body(st.pool[g]).split()) for g in st.gold_first])
    nsec = torch.bincount(sec_owner, minlength=n_docs).cpu().numpy()
    log_nsec = np.log1p(nsec[st.gold_first]).astype(float)
    controls = [cov, logw, log_nsec]

    clusters = gold_clusters(st.reqs)
    res = {"MRR@10": {k: st.macro(v) * 100 for k, v in rr.items()}, "mean_dilution_loss": float(loss.mean()),
           "partial_corr": {}, "fifths": []}
    for k, y in rr.items():
        stat = lambda ix, y=y: partial_corr(loss[ix], y[ix], [c[ix] for c in controls])
        res["partial_corr"][k] = {"r": stat(np.arange(len(y))), "ci": cluster_bootstrap(stat, clusters, a.n_boot)}
    cut = np.quantile(loss, np.linspace(0, 1, 6))
    for f in range(5):
        m = (loss >= cut[f]) & ((loss <= cut[f + 1]) if f == 4 else (loss < cut[f + 1]))
        res["fifths"].append({"mean_loss": float(loss[m].mean()), **{k: float(v[m].mean() * 100) for k, v in rr.items()}})

    print("MRR@10 " + "  ".join(f"{k} {v:.2f}" for k, v in res["MRR@10"].items()))
    print(f"mean dilution loss {res['mean_dilution_loss']:.4f}")
    print("partial correlation of RR with the dilution loss (controls: coverage, log words, log sections)")
    for k, d in res["partial_corr"].items():
        print(f"  {k:<11} {d['r']:+.3f} [{d['ci'][0]:+.3f}, {d['ci'][1]:+.3f}]")
    print("RR by fifths of the dilution loss")
    for row in res["fifths"]:
        print(f"  l {row['mean_loss']:.3f}  " + "  ".join(f"{k} {row[k]:.1f}" for k in rr))
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()
