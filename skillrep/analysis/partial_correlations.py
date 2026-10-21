#!/usr/bin/env python3
"""Partial correlations of module gains with the measures, correlations between the measures and
statistics of the dilution loss, from the outputs of quantities.py and module_gains.py.

Partial correlations: for each module and each measure, the partial correlation of the module's per-request
gain with the measure, controlling for the request's RR before the module is added and for the other
measures. The measures are one count of coverage, the B_D and B_Q gaps and the dilution loss; each
coverage column comes from the run with that count, the other columns from the run with lexical coverage.
Correlations: Pearson correlations between the six measures.
Dilution: mean dilution loss and its correlation with the gold document's length and with coverage.

Example:
  python analysis/partial_correlations.py --quantities results/quantities.npz \
      --gains results/module_gains.npz --out results/partial_correlations.json
Add --requests data/sra/requests.jsonl --n_boot 2000 for 95% intervals that resample gold-skill clusters.
"""
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from skillrep.data import load_jsonl  # noqa: E402
from skillrep.measures import cluster_bootstrap, gold_clusters, partial_corr  # noqa: E402

MODULES = ["finetuning", "query_expansion", "doc_expansion", "background_projection", "lexical_channel"]
COVERAGES = ["lexical", "semantic", "soft"]
OTHERS = ["gap_bd", "gap_bq", "dilution"]
COLUMNS = COVERAGES + OTHERS


def run(q, g, coverage, clusters=None, n_boot=0):
    """Partial correlations of every module's gain with each of coverage, gap_bd, gap_bq, dilution."""
    meas = {"coverage": q[coverage], **{k: q[k] for k in OTHERS}}
    out = {}
    for m in MODULES:
        y, base = g[f"{m}_gain"], g[f"{m}_base"]
        row = {}
        for k in meas:
            controls = [meas[o] for o in meas if o != k] + [base]
            row[k] = {"rho": partial_corr(meas[k], y, controls)}
            if clusters is not None and n_boot > 0:
                row[k]["ci"] = cluster_bootstrap(
                    lambda ix: partial_corr(meas[k][ix], y[ix], [c[ix] for c in controls]), clusters, n_boot)
        out[m] = row
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quantities", required=True, help="output of analysis/quantities.py")
    ap.add_argument("--gains", required=True, help="output of analysis/module_gains.py")
    ap.add_argument("--requests", help="requests.jsonl, needed for bootstrap intervals")
    ap.add_argument("--n_boot", type=int, default=0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    q, g = dict(np.load(a.quantities)), dict(np.load(a.gains))
    clusters = gold_clusters(load_jsonl(a.requests)) if a.requests else None

    runs = {c: run(q, g, c, clusters, a.n_boot) for c in COVERAGES}
    table = {m: {**{c: runs[c][m]["coverage"]["rho"] for c in COVERAGES},
                 **{k: runs["lexical"][m][k]["rho"] for k in OTHERS}} for m in MODULES}
    print("Partial correlation of each module's gain with each measure")
    print(f"{'':<24}" + "".join(f"{c:>10}" for c in COLUMNS))
    for m in MODULES:
        print(f"{m:<24}" + "".join(f"{table[m][c]:>+10.3f}" for c in COLUMNS))

    corr = np.corrcoef(np.stack([q[c] for c in COLUMNS]))
    separate = {a_: {b: float(corr[i, j]) for j, b in enumerate(COLUMNS)} for i, a_ in enumerate(COLUMNS)}
    print("\nPearson correlations between the measures")
    print(f"{'':<12}" + "".join(f"{c:>10}" for c in COLUMNS))
    for i, c in enumerate(COLUMNS):
        print(f"{c:<12}" + "".join(f"{corr[i, j]:>10.2f}" if j >= i else " " * 10 for j in range(len(COLUMNS))))

    loss = q["dilution"]
    dil = {"mean": float(loss.mean()), "median": float(np.median(loss)),
           "corr_log_words": float(np.corrcoef(loss, q["log_words"])[0, 1]),
           **{f"corr_{c}": float(np.corrcoef(loss, q[c])[0, 1]) for c in COVERAGES}}
    print("\nDilution loss " + ", ".join(f"{k} {v:+.3f}" for k, v in dil.items()))

    with open(a.out, "w") as f:
        json.dump({"table7": table, "runs": runs, "separability": separate, "dilution": dil}, f, indent=1)


if __name__ == "__main__":
    main()
