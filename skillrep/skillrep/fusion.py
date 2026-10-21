"""Fusing the dense and lexical channels.

Each channel returns its top-100 candidates per request. Score fusion standardizes each channel's
scores against the mean and standard deviation of its own top-100 for that request and adds them,
the lexical term weighted by alpha; a document that a channel does not return gets no score from it.
Rank fusion (RRF) is kept for comparison (analysis/fusion.py).
"""
import numpy as np

RRF_K = 60


def score_fusion(dense_ids, dense_scores, lex_ids=None, lex_scores=None, alpha=0.3):
    fused = {}
    d = np.asarray(dense_scores, dtype=np.float64)
    mu, sd = float(d.mean()), float(d.std()) + 1e-9
    for s, j in zip(d.tolist(), dense_ids):
        fused[int(j)] = fused.get(int(j), 0.0) + (s - mu) / sd
    if lex_ids is not None and alpha > 0:
        b = np.asarray(lex_scores, dtype=np.float64)
        mu, sd = float(b.mean()), float(b.std()) + 1e-9
        for s, j in zip(b.tolist(), lex_ids):
            fused[int(j)] = fused.get(int(j), 0.0) + alpha * (s - mu) / sd
    return sorted(fused, key=lambda j: -fused[j])


def rank_fusion(dense_ids, lex_ids=None, alpha=0.3, k=RRF_K):
    fused = {}
    for r, j in enumerate(dense_ids):
        fused[int(j)] = fused.get(int(j), 0.0) + 1.0 / (k + r + 1)
    if lex_ids is not None and alpha > 0:
        for r, j in enumerate(lex_ids):
            fused[int(j)] = fused.get(int(j), 0.0) + alpha / (k + r + 1)
    return sorted(fused, key=lambda j: -fused[j])
