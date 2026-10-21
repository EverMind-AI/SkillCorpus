"""SkillRep retrieval (Section 3, Figure 2).

Documents: document-side expansion (mix in the mean pseudo-request with weight u), then background
projection (B_D projected out with rescaling, then two generic-word directions subtracted without
rescaling). Requests: query-side expansion (mix in the mean hypothetical skill with weight w), then the
same generic-word directions subtracted. Dense top-100 and BM25 top-100 are fused by score (Eq. 1).

Embedding inputs are unit-norm float tensors:
  doc_emb      [N, d]  skill bodies, encoded as documents
  query_emb    [M, d]  requests, encoded with the instruction
  pseudo       (emb [K, d], owner [K])  pseudo-requests, owner = document index
  hypothetical (emb [K, d], owner [K])  hypothetical skills, owner = request index
"""
from dataclasses import dataclass

import numpy as np
import torch
import torch.nn.functional as F

from .background import generic_words, project_out, svd_directions, generic_basis
from .data import lexical_text
from .fusion import rank_fusion, score_fusion
from .lexical import BM25, lexical_queries


@dataclass
class SkillRepConfig:
    w: float = 0.3                # query-side expansion weight (0 turns the module off)
    u: float = 0.2                # document-side expansion weight (0 turns the module off)
    svd_rank: int = 2             # background projection, step 1 (0 turns it off)
    generic_rank: int = 2         # background projection, step 2 (0 turns it off)
    lexical: bool = True          # lexical channel
    lexical_hypothetical: str = "auto"  # BM25 query adds hypothetical skills: "auto" (when w > 0), "always", "never"
    alpha: float = 0.3            # weight of the lexical channel
    fusion: str = "score"         # "score" (Eq. 1) or "rank" (RRF, Appendix B)
    topn: int = 100               # candidates per channel
    topk: int = 50                # candidates kept after fusion


def mix(base, extra, owner, weight):
    """n((1 - weight) * base_i + weight * mean of the extra rows owned by i), for rows that own any."""
    if extra is None or weight <= 0:
        return base
    owner = torch.as_tensor(owner, dtype=torch.long, device=base.device)
    extra = F.normalize(extra.to(base.device).float(), dim=-1)
    sums = torch.zeros_like(base).index_add_(0, owner, extra)
    counts = torch.zeros(base.shape[0], device=base.device).index_add_(
        0, owner, torch.ones(len(owner), device=base.device))
    has = counts > 0
    out = base.clone()
    out[has] = F.normalize((1 - weight) * base[has] + weight * sums[has] / counts[has, None], dim=-1)
    return out


class SkillRep:
    def __init__(self, pool, doc_emb, config=None, pseudo=None, device="cuda:0", generic=None):
        """pool: list of skills (see data.py); generic: optional precomputed (words, inverted index)."""
        self.cfg = config or SkillRepConfig()
        self.pool = pool
        self.pids = [s["pid"] for s in pool]
        self.device = device
        self._bm25 = None
        if self.cfg.u > 0 and pseudo is None:
            raise ValueError("u > 0 needs pseudo-request embeddings (pseudo.pt); set u = 0 to switch the module off")
        docs = F.normalize(doc_emb.to(device).float(), dim=-1)
        if pseudo is not None:
            docs = mix(docs, pseudo[0], pseudo[1], self.cfg.u)
        self.b_d = None
        if self.cfg.svd_rank > 0:
            self.b_d = svd_directions(docs, self.cfg.svd_rank)
            docs = project_out(docs, self.b_d, rescale=True)
        self.b_g = None
        if self.cfg.generic_rank > 0:
            words, inv = generic if generic is not None else generic_words([lexical_text(s) for s in pool])
            self.b_g = generic_basis(docs, words, inv, self.cfg.generic_rank)
            docs = project_out(docs, self.b_g, rescale=False)
        self.docs = docs

    @property
    def bm25(self):
        if self._bm25 is None:
            self._bm25 = BM25([lexical_text(s) for s in self.pool])
        return self._bm25

    def encode_requests(self, query_emb, hypothetical=None):
        q = F.normalize(query_emb.to(self.device).float(), dim=-1)
        if hypothetical is not None:
            q = mix(q, hypothetical[0], hypothetical[1], self.cfg.w)
        if self.b_g is not None:
            q = project_out(q, self.b_g, rescale=False)
        return q

    def dense_topn(self, q, batch=512):
        ids, scores = [], []
        for i in range(0, q.shape[0], batch):
            v, j = (q[i:i + batch] @ self.docs.T).topk(min(self.cfg.topn, self.docs.shape[0]), dim=1)
            ids.append(j.cpu().numpy())
            scores.append(v.float().cpu().numpy())
        return np.concatenate(ids), np.concatenate(scores)

    def search(self, requests, query_emb, hypothetical=None, hypothetical_texts=None, lexical_hits=None):
        """Returns one ranked pid list (top-k) per request.

        lexical_hits: optional precomputed (ids, scores) of the BM25 channel, e.g. to reuse across runs."""
        cfg = self.cfg
        if cfg.w > 0 and hypothetical is None:
            raise ValueError("w > 0 needs hypothetical-skill embeddings (hypothetical.pt); set w = 0 to switch the module off")
        q = self.encode_requests(query_emb, hypothetical if cfg.w > 0 else None)
        d_ids, d_scores = self.dense_topn(q)
        l_ids = l_scores = None
        if cfg.lexical and cfg.alpha > 0:
            if lexical_hits is None:
                use_hyp = cfg.lexical_hypothetical == "always" or (cfg.lexical_hypothetical == "auto" and cfg.w > 0)
                if use_hyp and hypothetical_texts is None:
                    raise ValueError("the lexical channel searches with the hypothetical skills here; pass "
                                     "hypothetical_texts or set lexical_hypothetical='never'")
                texts = lexical_queries(requests, hypothetical_texts if use_hyp else None)
                lexical_hits = self.bm25.search(texts, cfg.topn)
            l_ids, l_scores = lexical_hits
        rankings = []
        for i in range(len(requests)):
            li = l_ids[i] if l_ids is not None else None
            ls = l_scores[i] if l_scores is not None else None
            if cfg.fusion == "score":
                order = score_fusion(d_ids[i], d_scores[i], li, ls, cfg.alpha)
            else:
                order = rank_fusion(d_ids[i], li, cfg.alpha)
            rankings.append([self.pids[j] for j in order[:cfg.topk]])
        return rankings
