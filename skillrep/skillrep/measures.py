"""The three per-request quantities of Section 6 and the statistics of Section 7.

Background (Section 6.1)
  B_D: top right singular vectors of the uncentered document matrix.
  B_Q: top right singular vector of the uncentered pseudo-request matrix, signed so that requests
       have a positive mean component on it.
  background gap: for a request, the mean background score <P_B q, d> of the non-gold documents in
       its top ten minus that of its gold document (x 100).
Unsaid vocabulary (Section 6.2)
  coverage: the share of the gold document's content words that the request expresses, counted
       lexically, semantically (a request word within cosine tau of the gold word) or softly.
Dilution (Section 6.3)
  dilution loss: cos(q, s*) - cos(q, d), with s* the gold section most similar to q.
"""
import re

import numpy as np
import torch

# Content words for coverage and the word-level analyses (not the BM25 tokenizer of lexical.py).
CONTENT_STOP = set("""the a an and or of to in for with on at by from as is are be was were this that these those
it its into your you i we they he she his her their our not no if then than so such can could may might
will would shall should must do does did done have has had been being but about over under more most
other some any each both few all own same too very just only also then once here there when where why how
what which who whom whose use using used make makes made get gets got please need needs want wants""".split())
CONTENT_WORD = re.compile(r"[a-z][a-z0-9_]{2,}")
GOLD_BODY_MAX = 8000


def content_words(text):
    """Content words in order of appearance (with repeats)."""
    return [w for w in CONTENT_WORD.findall((text or "").lower()) if w not in CONTENT_STOP]


def gold_body(skill):
    return (skill.get("body") or "")[:GOLD_BODY_MAX]


# ---------------------------------------------------------------- background

def document_background(docs, k=2):
    """B_D [k, d]."""
    return torch.linalg.svd(docs.float(), full_matrices=False)[2][:k]


def request_background(pseudo_emb, query_emb):
    """B_Q [1, d], signed so that the requests' mean component is positive."""
    b = torch.linalg.svd(pseudo_emb.float(), full_matrices=False)[2][:1]
    if (query_emb.to(b.device) @ b[0]).mean() < 0:
        b = -b
    return b


def top_k(queries, docs, k=10, batch=512):
    out = []
    for s in range(0, queries.shape[0], batch):
        out.append((queries[s:s + batch] @ docs.T).topk(k, dim=1).indices.cpu())
    return torch.cat(out).tolist()


def background_gap(queries, docs, basis, top, gold_sets, gold_first):
    """Per-request background gap (x 100); NaN when the top ten holds only gold documents.

    top: per request, the indices of its top-ten documents under the retriever being measured."""
    sq, sd = queries @ basis.T, docs @ basis.T
    out = np.full(len(top), np.nan)
    for i, row in enumerate(top):
        comp = [j for j in row if j not in gold_sets[i]]
        if comp:
            score = (sq[i] * sd[comp]).sum(1).mean() - (sq[i] * sd[gold_first[i]]).sum()
            out[i] = float(score) * 100
    return out


def background_rank(queries, docs, basis, gold_first, batch=64):
    """Share of the library (%) that scores above the gold document under <P_B q, d> alone."""
    pq = (queries @ basis.T) @ basis
    out = []
    for s in range(0, pq.shape[0], batch):
        sc = pq[s:s + batch] @ docs.T
        g = torch.as_tensor(gold_first[s:s + batch], device=sc.device)
        gs = sc.gather(1, g[:, None])
        out.append(((sc > gs).float().mean(1) * 100).cpu().numpy())
    return np.concatenate(out)


# ---------------------------------------------------------------- unsaid vocabulary

def lexical_coverage(request_words, gold_words):
    g = set(gold_words)
    return len(g & set(request_words)) / max(1, len(g))


@torch.no_grad()
def word_embeddings(encoder, words, batch_size=512):
    """Each word embedded on its own, without an instruction (the untuned encoder in the paper), in the given
    order and batches of 512 (the paper's sorted vocabulary), since bfloat16 results depend on the batches."""
    return encoder(list(words), batch_size=batch_size, max_length=32, sort_by_length=False)


def semantic_coverage(request_words, gold_words, word_emb, word_id, taus=(0.8,)):
    """Returns ({tau: semantic coverage}, soft coverage). word_emb rows are unit-norm."""
    gw, qw = sorted(set(gold_words)), sorted(set(request_words))
    if not gw or not qw:
        return {t: 0.0 for t in taus}, 0.0
    sim = (word_emb[[word_id[w] for w in gw]] @ word_emb[[word_id[w] for w in qw]].T).max(1).values.cpu().numpy()
    return {t: float((sim >= t).mean()) for t in taus}, float(sim.mean())


# ---------------------------------------------------------------- dilution

SECTION_MIN_CHARS = 200
WINDOW_CHARS = 1200


def split_sections(body):
    """Splits a body at markdown headings (levels 1-3), dropping pieces under 200 characters. With fewer
    than two sections, falls back to windows of about 1,200 characters built from whole paragraphs."""
    parts = [p.strip() for p in re.split(r"\n(?=#{1,3} )", body) if len(p.strip()) >= SECTION_MIN_CHARS]
    if len(parts) >= 2:
        return parts
    paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    out, cur = [], ""
    for p in paras:
        cur = (cur + "\n\n" + p) if cur else p
        if len(cur) >= WINDOW_CHARS:
            out.append(cur)
            cur = ""
    if cur.strip():
        out.append(cur)
    return [p for p in out if len(p.strip()) >= SECTION_MIN_CHARS] or [body]


def dilution_loss(queries, doc_emb, section_emb, section_owner, gold_first):
    """Per-request cos(q, s*) - cos(q, d) for the gold document gold_first[i] (a document index).
    section_owner[k] is the document index of section row k."""
    owner = np.asarray(section_owner)
    rows = {d: np.nonzero(owner == d)[0] for d in set(gold_first)}
    out = np.zeros(len(gold_first))
    for i, d in enumerate(gold_first):
        best = float((section_emb[rows[d]] @ queries[i]).max())
        out[i] = best - float(doc_emb[d] @ queries[i])
    return out


# ---------------------------------------------------------------- statistics

def partial_corr(x, y, controls):
    """Correlation of x and y after removing least-squares linear fits on the controls (Eq. 3)."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    z = np.column_stack([np.ones_like(x)] + [np.asarray(c, float) for c in controls])
    rx = x - z @ np.linalg.lstsq(z, x, rcond=None)[0]
    ry = y - z @ np.linalg.lstsq(z, y, rcond=None)[0]
    return float(np.corrcoef(rx, ry)[0, 1])


def gold_clusters(requests):
    """Requests grouped by (dataset, gold set), the unit of the cluster bootstrap."""
    cl = {}
    for i, r in enumerate(requests):
        cl.setdefault((r.get("dataset"), tuple(sorted(r["gt_pids"]))), []).append(i)
    return [np.array(v) for v in cl.values()]


def cluster_bootstrap(stat, clusters, n=2000, seed=0):
    """95% interval of stat(index array) over resampled clusters."""
    rng = np.random.default_rng(seed)
    vals = [stat(np.concatenate([clusters[j] for j in rng.integers(0, len(clusters), len(clusters))]))
            for _ in range(n)]
    return [float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))]
