"""Shared setup for the analyses of Sections 6-7 and the appendices.

All analyses take one pool, one request set and the embedding directory written by scripts/encode.py
for one encoder (the paper uses SRA-Bench's 5,400 requests on UniPool). Per-request reciprocal ranks
are in [0, 1]; tables report them x 100.
"""
import os
import sys

import numpy as np
import torch
import torch.nn.functional as F

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from skillrep.background import generic_basis, generic_words, project_out, svd_directions  # noqa: E402
from skillrep.data import lexical_text, load_embeddings, load_json, load_jsonl, macro_groups  # noqa: E402
from skillrep.fusion import score_fusion  # noqa: E402
from skillrep.lexical import BM25, lexical_queries  # noqa: E402
from skillrep.pipeline import mix  # noqa: E402

W, U, ALPHA, TOPN = 0.3, 0.2, 0.3, 100


def add_setting_args(ap, encoders=("ft",)):
    ap.add_argument("--pool", required=True, help="pool.jsonl (UniPool in the paper)")
    ap.add_argument("--requests", required=True, help="requests.jsonl (SRA-Bench in the paper)")
    ap.add_argument("--hypothetical", help="hypothetical skills JSON (texts), for the lexical channel")
    for name in encoders:
        ap.add_argument(f"--emb_{name}", required=True, help=f"scripts/encode.py output for the {name} encoder")
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--out", required=True, help="output JSON")


class Setting:
    """One pool and one request set, with gold documents as pool indices."""

    def __init__(self, pool_path, requests_path, hypothetical_path=None):
        self.pool = load_jsonl(pool_path)
        self.reqs = load_jsonl(requests_path)
        self.pid2i = {s["pid"]: i for i, s in enumerate(self.pool)}
        self.gold = [{self.pid2i[p] for p in r["gt_pids"] if p in self.pid2i} for r in self.reqs]
        self.gold_first = [self.pid2i[r["gt_pids"][0]] for r in self.reqs]
        self.groups = macro_groups(self.reqs)
        self.hyp_texts = load_json(hypothetical_path) if hypothetical_path else None
        self._generic = self._bm25 = None
        self._hits = {}

    def macro(self, values):
        """Macro average over source datasets."""
        g = np.asarray(self.groups)
        return float(np.mean([np.mean(values[g == k]) for k in sorted(set(self.groups))]))

    @property
    def generic(self):
        if self._generic is None:
            self._generic = generic_words([lexical_text(s) for s in self.pool])
        return self._generic

    @property
    def bm25(self):
        if self._bm25 is None:
            self._bm25 = BM25([lexical_text(s) for s in self.pool])
        return self._bm25

    def lexical_hits(self, with_hypothetical):
        """BM25 top-100 (ids, scores) for the request alone or the request plus its hypothetical skills."""
        if with_hypothetical not in self._hits:
            texts = lexical_queries(self.reqs, self.hyp_texts if with_hypothetical else None)
            self._hits[with_hypothetical] = self.bm25.search(texts, TOPN)
        return self._hits[with_hypothetical]


class Embeddings:
    """Unit-norm embeddings of one encoder on a Setting, on one device."""

    def __init__(self, setting, emb_dir, device):
        e = load_embeddings(emb_dir, setting.reqs, setting.pool)
        self.device = device
        self.docs = F.normalize(e["docs"].to(device), dim=-1)
        self.queries = F.normalize(e["requests"].to(device), dim=-1)
        self.hypothetical = self._pair(e.get("hypothetical"))
        self.pseudo = self._pair(e.get("pseudo"))

    def _pair(self, p):
        return None if p is None else (F.normalize(p[0].to(self.device), dim=-1), p[1].to(self.device))

    # the modules, applied to embeddings exactly as in skillrep.pipeline
    def expand_queries(self, q=None, w=W):
        return mix(self.queries if q is None else q, self.hypothetical[0], self.hypothetical[1], w)

    def expand_docs(self, d=None, u=U):
        return mix(self.docs if d is None else d, self.pseudo[0], self.pseudo[1], u)


def background_projection(docs, generic, svd_rank=2, generic_rank=2, rescale_step2=False):
    """Two-level background projection of the documents. Returns (docs, B_D, generic basis); requests
    need only the generic basis removed, which does not change any ranking."""
    b_d = svd_directions(docs, svd_rank)
    x = project_out(docs, b_d, rescale=True)
    b_g = generic_basis(x, generic[0], generic[1], generic_rank) if generic_rank > 0 else None
    if b_g is not None:
        x = project_out(x, b_g, rescale=rescale_step2)
    return x, b_d, b_g


@torch.no_grad()
def dense_topn(q, docs, n=TOPN, batch=512):
    ids, scores = [], []
    for s in range(0, q.shape[0], batch):
        v, j = (q[s:s + batch] @ docs.T).topk(min(n, docs.shape[0]), dim=1)
        ids.append(j.cpu().numpy())
        scores.append(v.float().cpu().numpy())
    return np.concatenate(ids), np.concatenate(scores)


def rr_from_rankings(rankings, gold, k=10):
    out = np.zeros(len(rankings))
    for i, rk in enumerate(rankings):
        for r, j in enumerate(rk[:k]):
            if j in gold[i]:
                out[i] = 1.0 / (r + 1)
                break
    return out


def rankings(q, docs, hits=None, alpha=ALPHA, k=10):
    """Top-k document indices per request: dense alone, or fused with BM25 hits by score (Eq. 1)."""
    d_ids, d_sc = dense_topn(q, docs)
    if hits is None:
        return [list(map(int, r[:k])) for r in d_ids]
    l_ids, l_sc = hits
    return [score_fusion(d_ids[i], d_sc[i], l_ids[i], l_sc[i], alpha)[:k] for i in range(len(d_ids))]


def rr(setting, q, docs, hits=None, alpha=ALPHA):
    """Per-request RR@10 in [0, 1]."""
    return rr_from_rankings(rankings(q, docs, hits, alpha), setting.gold)
