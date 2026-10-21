"""Helpers shared by the dilution analyses (Figure 3, Table 8) and the lexical-channel mechanism (Section 7.3).

Three ways of cutting a skill body into sections are used:
  index_sections    the section-level retriever's index: split at "##"/"###" headings, short pieces merged,
                    long pieces cut into overlapping windows, at most 8 per skill, each encoded with the
                    skill's name and description as a prefix
  heading_sections  split at "#" to "###" headings, pieces under 200 characters dropped (padding and the
                    word-deletion probes)
  measures.split_sections  as heading_sections, with a fallback to paragraph windows (the dilution loss
                    of Section 6.3)
The BM25 retriever of Figure 3 and Table 8 is the lexical channel's, searching with the request alone.
"""
import os
import re
import sys

import numpy as np
import torch
import torch.nn.functional as F

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from skillrep.lexical import BM25  # noqa: E402

# ---------------------------------------------------------------- sections

INDEX_BODY_MAX = 12000
INDEX_MAX_LENGTH = 1024
CHUNK_TARGET, CHUNK_MAX, CHUNK_MIN, CHUNK_OVERLAP, MAX_CHUNKS = 1200, 2000, 300, 120, 8


def _text(value):
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(str(x) for x in value)
    return str(value)


def index_sections(body):
    """Sections of the section-level index: split at ##/### headings, merge pieces under 300 characters,
    cut pieces over 2,000 characters into 1,200-character windows overlapping by 120, keep at most 8."""
    b = _text(body)[:INDEX_BODY_MAX].strip()
    if not b:
        return []
    merged = []
    for p in re.split(r"\n(?=#{2,3}\s)", b):
        p = p.strip()
        if not p:
            continue
        if merged and (len(p) < CHUNK_MIN or len(merged[-1]) < CHUNK_MIN):
            merged[-1] = merged[-1] + "\n" + p
        else:
            merged.append(p)
    out = []
    for p in merged:
        if len(p) <= CHUNK_MAX:
            out.append(p)
            continue
        i = 0
        while i < len(p):
            out.append(p[i:i + CHUNK_TARGET])
            i += CHUNK_TARGET - CHUNK_OVERLAP
    return out[:MAX_CHUNKS] or [b[:CHUNK_MAX]]


def section_prefix(skill):
    return f"{_text(skill.get('name'))} | {_text(skill.get('description'))[:300]} | "


def heading_sections(text, min_chars=200):
    return [p.strip() for p in re.split(r"\n(?=#{1,3} )", text) if len(p.strip()) >= min_chars]


def load_section_index(path, device, half=False):
    """Section index written by encode_sections.py: (unit-norm embeddings, owner document index)."""
    z = torch.load(path, weights_only=False)
    emb = F.normalize(z["emb"].float(), dim=-1)
    return (emb.half() if half else emb).to(device), z["owner"].long().to(device)


@torch.no_grad()
def best_section_scores(q, sec_emb, sec_owner, doc_emb):
    """[b, N] score of every document by its best section; documents without sections keep their
    whole-body score."""
    sim = (q.to(sec_emb.dtype) @ sec_emb.T).float()
    agg = torch.full((q.shape[0], doc_emb.shape[0]), -1e4, device=q.device)
    agg.scatter_reduce_(1, sec_owner.unsqueeze(0).expand(q.shape[0], -1), sim, reduce="amax")
    return torch.where(agg < -1e3, q @ doc_emb.T, agg)


def rr_from_top(top, gold, k=10):
    out = np.zeros(len(top))
    for i, row in enumerate(top):
        for r, j in enumerate(row[:k]):
            if int(j) in gold[i]:
                out[i] = 1.0 / (r + 1)
                break
    return out


# ---------------------------------------------------------------- BM25 with the request alone

def bm25_doc_text(skill, body=None, body_max=8000):
    """Name, description and body, as the lexical channel indexes a skill; body overrides the skill's own."""
    body = (skill.get("body") or "")[:body_max] if body is None else body
    return f"{_text(skill.get('name'))} {_text(skill.get('description'))} {body}"


def bm25_top(doc_texts, queries, n=10):
    """Top-n document indices per query under the lexical channel's BM25."""
    return BM25(doc_texts).search(queries, n)[0]
