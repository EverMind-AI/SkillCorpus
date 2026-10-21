"""Background projection (Section 3.3).

Step 1 removes B_D, the top right singular vectors of the uncentered document matrix, and rescales
the documents to unit length. Step 2 removes two directions estimated from the library's most generic
words and subtracts them without rescaling, from documents and requests alike.
"""
from collections import defaultdict

import numpy as np
import torch
import torch.nn.functional as F

from .lexical import tokenize


def svd_directions(x, k):
    """Top-k right singular vectors of the uncentered matrix x [n, d]; returns [k, d]."""
    return torch.linalg.svd(x.float(), full_matrices=False)[2][:k]


def project_out(x, basis, rescale):
    """Removes the span of the orthonormal rows of basis [k, d] from the rows of x."""
    x = x - (x @ basis.T) @ basis
    return F.normalize(x, dim=-1) if rescale else x


def otsu_threshold(values, weights=None, bins=256):
    hist, edges = np.histogram(values, bins=bins, weights=weights)
    hist = hist.astype(np.float64)
    if hist.sum() <= 0:
        return float(np.median(values))
    p = hist / hist.sum()
    c = np.cumsum(p)
    mids = (edges[:-1] + edges[1:]) / 2
    mu = np.cumsum(p * mids)
    with np.errstate(divide="ignore", invalid="ignore"):
        sigma = (mu[-1] * c - mu) ** 2 / (c * (1 - c))
    sigma[~np.isfinite(sigma)] = -1
    return float(mids[int(np.argmax(sigma))])


def generic_words(doc_texts):
    """Words with the lowest IDF, split from the rest by an Otsu threshold weighted by document frequency.

    Returns (generic word list, inverted index word -> sorted document indices)."""
    inv = defaultdict(list)
    for j, t in enumerate(doc_texts):
        for w in set(tokenize(t)):
            inv[w].append(j)
    vocab = list(inv)
    n = len(doc_texts)
    df = np.array([len(inv[w]) for w in vocab], np.float64)
    idf = np.log((n + 1) / (df + 1)) + 1.0
    thr = otsu_threshold(idf, weights=df)
    generic = sorted(vocab[i] for i in np.nonzero(idf <= thr)[0])
    return generic, {w: np.array(sorted(v), np.int64) for w, v in inv.items()}


def generic_basis(docs, generic, inv, k=2):
    """Top-k directions of the mean embeddings of the documents containing each generic word
    (plus the library mean); returns [k, d]."""
    dev = docs.device
    means = [F.normalize(docs.index_select(0, torch.from_numpy(inv[w]).to(dev)).mean(0), dim=-1)
             for w in generic]
    means.append(F.normalize(docs.mean(0), dim=-1))
    g = F.normalize(torch.stack(means), dim=-1)
    return torch.linalg.svd(g.double(), full_matrices=False)[2][:k].float()


def two_level_projection(docs, queries, doc_texts, svd_rank=2, generic_rank=2, generic=None):
    """Applies both steps. Returns (projected docs, projected queries, B_D, generic basis).

    Projecting the requests in step 1 is unnecessary: the projected documents lie in the orthogonal
    complement of B_D, so the ranking does not change."""
    b_d = svd_directions(docs, svd_rank) if svd_rank > 0 else None
    if b_d is not None:
        docs = project_out(docs, b_d, rescale=True)
    b_g = None
    if generic_rank > 0:
        words, inv = generic if generic is not None else generic_words(doc_texts)
        b_g = generic_basis(docs, words, inv, generic_rank)
        docs = project_out(docs, b_g, rescale=False)
        queries = project_out(queries, b_g, rescale=False)
    return docs, queries, b_d, b_g
