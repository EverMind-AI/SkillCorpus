#!/usr/bin/env python3
"""The three quantities on skills and on general retrieval collections (Appendix F).

Every collection is encoded with the untuned Qwen3-Embedding-0.6B and searched with the dense channel
only. Per collection the script reports:
  mrr10              MRR@10 of the dense channel (micro average)
  projection         MRR@10 change from projecting B_D (top-2 SVD, rescaled) out of the documents
  share_of_BD        mean squared length of a document along B_D
  above_gold         share of the collection scoring above the gold document on <q, P_BD d> alone
  gap                background gap along B_D (Table 8), x 100
  request_words      median number of content words per request
  coverage_gold_side share of the gold document's content words that the request expresses
  coverage_request_side  share of the request's content words found in its gold document
  gold_words         mean number of words of a gold document
  dilution_loss      cos(q, best window) - cos(q, d), with gold documents cut into 80-word windows
The gold document of a request is its first relevant document (pool order for skills; for BEIR, the
corpus order for the background and dilution measures and the sorted document ids for coverage).

Skills are given in our format (pool.jsonl, requests.jsonl and the scripts/encode.py output of the
untuned encoder, encoded with --instruction untuned). BEIR collections are given as standard BEIR
directories (corpus.jsonl, queries.jsonl, qrels/test.tsv); documents are "title text", requests carry
the web-search instruction of the Qwen3-Embedding model card.

Example:
  python analysis/beyond_skills.py --model Qwen/Qwen3-Embedding-0.6B \
      --skills_pool data/unipool/pool.jsonl --skills_requests data/sra/requests.jsonl \
      --skills_emb emb/unipool_sra_untuned \
      --beir scifact=data/beir/scifact --beir nfcorpus=data/beir/nfcorpus --beir fiqa=data/beir/fiqa \
      --cache_dir emb/beir --out results/beyond_skills.json
"""
import argparse
import csv
import json
import os
import sys

import numpy as np
import torch
import torch.nn.functional as F

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from skillrep.background import project_out, svd_directions  # noqa: E402
from skillrep.data import load_embeddings, load_jsonl  # noqa: E402
from skillrep.encoder import Encoder  # noqa: E402
from skillrep.measures import content_words, gold_body  # noqa: E402

BEIR_INSTRUCTION = "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:"
WINDOW_WORDS = 80
DOC_MAX_LENGTH, QUERY_MAX_LENGTH = 1024, 512


def windows(text):
    w = text.split()
    return [" ".join(w[i:i + WINDOW_WORDS]) for i in range(0, max(len(w), 1), WINDOW_WORDS)] or [" "]


def coverage(pairs):
    """Gold-side and request-side lexical coverage over (request text, gold text) pairs."""
    gold_side, req_side, n_words = [], [], []
    for q, g in pairs:
        qw, gw = set(content_words(q)), set(content_words(g))
        if not qw or not gw:
            continue
        shared = len(qw & gw)
        gold_side.append(shared / len(gw))
        req_side.append(shared / len(qw))
        n_words.append(len(qw))
    return {"coverage_gold_side": float(np.mean(gold_side)), "coverage_request_side": float(np.mean(req_side)),
            "request_words": float(np.median(n_words))}


@torch.no_grad()
def reciprocal_ranks(q, docs, gold, batch=256):
    out = np.zeros(q.shape[0])
    for s in range(0, q.shape[0], batch):
        top = (q[s:s + batch] @ docs.T).topk(10, dim=1).indices.cpu().numpy()
        for i, row in enumerate(top):
            for k, j in enumerate(row):
                if int(j) in gold[s + i]:
                    out[s + i] = 1.0 / (k + 1)
                    break
    return out


@torch.no_grad()
def measure(docs, queries, gold, gold_first, gold_texts, encoder):
    """docs, queries: unit-norm tensors on one device; gold: sets of document indices;
    gold_first[i]: the gold document index used for the background and dilution measures, whose text is
    gold_texts[i]."""
    dev = docs.device
    r = {"n_docs": int(docs.shape[0]), "n_requests": int(queries.shape[0])}
    b_d = svd_directions(docs, 2)
    base = reciprocal_ranks(queries, docs, gold)
    r["mrr10"] = float(base.mean() * 100)
    r["projection"] = float((reciprocal_ranks(queries, project_out(docs, b_d, rescale=True), gold) - base).mean() * 100)
    r["share_of_BD"] = float(((docs @ b_d.T) ** 2).sum(1).mean())

    above, gap = [], []
    g_idx = torch.tensor(gold_first, device=dev)
    sd = docs @ b_d.T
    for s in range(0, queries.shape[0], 128):
        q = queries[s:s + 128]
        bscore = (q @ b_d.T) @ sd.T                      # background score of every document
        gs = bscore[torch.arange(q.shape[0], device=dev), g_idx[s:s + 128]]
        above += (bscore > gs[:, None]).float().mean(1).tolist()
        top = (q @ docs.T).topk(10, dim=1).indices
        for i in range(q.shape[0]):
            comp = [int(j) for j in top[i].tolist() if int(j) not in gold[s + i]]
            if comp:
                gap.append((bscore[i, comp].mean() - gs[i]).item() * 100)
    r["above_gold"] = float(np.mean(above))
    r["gap"] = float(np.mean(gap))

    # dilution with uniform 80-word windows (BEIR documents have no sections)
    golds = sorted(set(gold_first))
    text_of = dict(zip(gold_first, gold_texts))
    win_text, win_owner = [], []
    for g in golds:
        for w in windows(text_of[g]):
            win_text.append(w)
            win_owner.append(g)
    win_emb = encoder(win_text, batch_size=16, max_length=DOC_MAX_LENGTH).to(dev)
    win_owner = np.asarray(win_owner)
    rows = {g: torch.from_numpy(np.nonzero(win_owner == g)[0]).to(dev) for g in golds}
    loss = [float((win_emb[rows[g]] @ queries[i]).max() - docs[g] @ queries[i]) for i, g in enumerate(gold_first)]
    r["dilution_loss"] = float(np.mean(loss))
    r["gold_words"] = float(np.mean([len(text_of[g].split()) for g in golds]))
    return r


def load_beir(path):
    corpus = load_jsonl(os.path.join(path, "corpus.jsonl"))
    ids = [d["_id"] for d in corpus]
    index = {d: k for k, d in enumerate(ids)}
    texts = [((d.get("title") or "") + " " + (d.get("text") or "")).strip() for d in corpus]
    queries = {q["_id"]: q["text"] for q in load_jsonl(os.path.join(path, "queries.jsonl"))}
    qrels = {}
    with open(os.path.join(path, "qrels", "test.tsv")) as f:
        rd = csv.reader(f, delimiter="\t")
        next(rd)
        for qid, did, score in rd:
            if int(score) > 0 and did in index:
                qrels.setdefault(qid, []).append(did)
    qids = [q for q in qrels if q in queries]
    return ids, index, texts, queries, qrels, qids


def run_beir(name, path, encoder, device, cache_dir):
    ids, index, texts, queries, qrels, qids = load_beir(path)
    cache = os.path.join(cache_dir, f"{name}_docs.pt") if cache_dir else None
    if cache and os.path.exists(cache):
        docs = torch.load(cache, weights_only=False)
        docs = docs["emb"] if isinstance(docs, dict) else docs
    else:
        docs = encoder(texts, batch_size=16, max_length=DOC_MAX_LENGTH)
        if cache:
            os.makedirs(cache_dir, exist_ok=True)
            torch.save({"emb": docs, "ids": ids}, cache)
    docs = F.normalize(docs.float(), dim=-1).to(device)
    queries_emb = encoder([BEIR_INSTRUCTION + queries[q] for q in qids], batch_size=16, max_length=QUERY_MAX_LENGTH)
    queries_emb = F.normalize(queries_emb.float(), dim=-1).to(device)
    gold = [{index[d] for d in qrels[q]} for q in qids]
    gold_first = [min(g) for g in gold]
    r = measure(docs, queries_emb, gold, gold_first, [texts[g] for g in gold_first], encoder)
    r.update(coverage([(queries[q], texts[index[sorted(qrels[q])[0]]]) for q in qids]))
    return r


def run_skills(pool_path, requests_path, emb_dir, encoder, device):
    pool, reqs = load_jsonl(pool_path), load_jsonl(requests_path)
    emb = load_embeddings(emb_dir, reqs, pool)
    pid2i = {s["pid"]: i for i, s in enumerate(pool)}
    docs = F.normalize(emb["docs"].to(device), dim=-1)
    queries = F.normalize(emb["requests"].to(device), dim=-1)
    gold = [{pid2i[p] for p in r["gt_pids"] if p in pid2i} for r in reqs]
    gold_first = [pid2i[r["gt_pids"][0]] for r in reqs]
    gold_texts = [gold_body(pool[g]) for g in gold_first]
    r = measure(docs, queries, gold, gold_first, gold_texts, encoder)
    r.update(coverage([(q["query"], t) for q, t in zip(reqs, gold_texts)]))
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Qwen/Qwen3-Embedding-0.6B", help="untuned encoder")
    ap.add_argument("--skills_pool")
    ap.add_argument("--skills_requests")
    ap.add_argument("--skills_emb", help="scripts/encode.py output of the untuned encoder")
    ap.add_argument("--beir", action="append", default=[], help="name=path of a BEIR collection")
    ap.add_argument("--cache_dir", help="where BEIR document embeddings are cached")
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    res = {}
    if os.path.exists(a.out):  # resume collection by collection
        with open(a.out) as f:
            res = json.load(f)
    encoder = Encoder(a.model, a.device)

    def save(name, r):
        res[name] = r
        print(name, json.dumps(r), flush=True)
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w") as f:
            json.dump(res, f, indent=1)

    if a.skills_pool and "skills" not in res:
        save("skills", run_skills(a.skills_pool, a.skills_requests, a.skills_emb, encoder, a.device))
    for spec in a.beir:
        name, path = spec.split("=", 1)
        if name not in res:
            save(name, run_beir(name, path, encoder, a.device, a.cache_dir))
        torch.cuda.empty_cache()

    rows = [("Documents", "n_docs", "{:,.0f}"), ("Requests", "n_requests", "{:,.0f}"), ("MRR@10", "mrr10", "{:.2f}"),
            ("Share of B_D", "share_of_BD", "{:.2f}"), ("Above gold", "above_gold", "{:.0%}"), ("Gap", "gap", "{:.2f}"),
            ("Projection", "projection", "{:+.2f}"), ("Request words", "request_words", "{:.0f}"),
            ("Coverage, gold side", "coverage_gold_side", "{:.3f}"),
            ("Coverage, request side", "coverage_request_side", "{:.2f}"),
            ("Gold words", "gold_words", "{:.0f}"), ("Dilution loss", "dilution_loss", "{:.3f}")]
    print("\n" + f"{'':<24}" + "".join(f"{n:>12}" for n in res))
    for label, key, fmt in rows:
        print(f"{label:<24}" + "".join(f"{fmt.format(r[key]):>12}" for r in res.values()))


if __name__ == "__main__":
    main()
