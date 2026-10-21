"""R@10, MRR@10 and Hit@1, per request and macro-averaged over source datasets (Section 4)."""
from collections import defaultdict

import numpy as np

from .data import macro_groups


def per_request(ranked, gold, k=10):
    """ranked: candidate document ids, best first; gold: iterable of gold ids. Values in [0, 1]."""
    g = set(gold)
    top = list(ranked[:k])
    rr = next((1.0 / (r + 1) for r, x in enumerate(top) if x in g), 0.0)
    return {"R@10": len(g & set(top)) / max(1, len(g)), "MRR@10": rr,
            "Hit@1": float(bool(ranked) and ranked[0] in g)}


def evaluate(rankings, requests, groups=None):
    """Macro average over groups (source datasets) of the per-request metrics, x 100.

    rankings: list of candidate pid lists aligned with requests; requests carry "gt_pids"."""
    if groups is None:
        groups = macro_groups(requests)
    per = defaultdict(lambda: defaultdict(list))
    for ranked, req, grp in zip(rankings, requests, groups):
        for m, v in per_request(ranked, req["gt_pids"]).items():
            per[m][grp].append(v)
    return {m: round(float(np.mean([np.mean(v) for v in by.values()]) * 100), 2) for m, by in per.items()}


def reciprocal_ranks(rankings, requests):
    """Per-request RR (x 100), used by the analyses."""
    return np.array([per_request(r, q["gt_pids"])["MRR@10"] * 100 for r, q in zip(rankings, requests)])


def paired_bootstrap(a, b, groups=None, n=10000, seed=0):
    """Mean difference a - b and its 95% interval from resampling requests (within groups if given)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    rng = np.random.default_rng(seed)
    d = a - b
    if groups is None:
        idx = rng.integers(0, len(d), (n, len(d)))
        boots = d[idx].mean(1)
        point = d.mean()
    else:
        groups = np.asarray(groups)
        members = [np.nonzero(groups == g)[0] for g in sorted(set(groups))]
        point = np.mean([d[m].mean() for m in members])
        boots = np.array([np.mean([d[m[rng.integers(0, len(m), len(m))]].mean() for m in members])
                          for _ in range(n)])
    return float(point), [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))]
