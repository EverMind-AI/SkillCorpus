#!/usr/bin/env python3
"""Per-request measures of the three quantities (Section 6), used by Table 7 and Appendix E.

For each request, with g its (first) gold document:
  lexical, semantic, soft  coverage of g's content words by the request (Section 6.2); semantic and soft
                           coverage embed every word on its own with the untuned encoder
  gap_bd, gap_bq           background gap along B_D and B_Q on the fine-tuned encoder, no other module
                           (Section 6.1, Table 6)
  dilution                 dilution loss cos(q, s*) - cos(q, d) (Eq. 2), sections encoded by the
                           fine-tuned encoder as documents
  log_words                log(1 + number of words) of g's body, for the length control

Example:
  python analysis/quantities.py --pool data/unipool/pool.jsonl --requests data/sra/requests.jsonl \
      --emb_ft emb/unipool_sra_ft --section_encoder path/to/finetuned-encoder \
      --npz results/quantities.npz --out results/quantities.json
"""
import argparse
import json

import numpy as np
import torch

from common import Embeddings, Setting, add_setting_args
from skillrep.encoder import Encoder
from skillrep.measures import (background_gap, content_words, dilution_loss, document_background, gold_body,
                               lexical_coverage, request_background, semantic_coverage, split_sections, top_k,
                               word_embeddings)

TAU = 0.8


def coverages(setting, word_encoder, device, batch_size):
    req_words = [content_words(r["query"]) for r in setting.reqs]
    gold_words = {g: content_words(gold_body(setting.pool[g])) for g in set(setting.gold_first)}
    lexical = np.array([lexical_coverage(req_words[i], gold_words[g]) for i, g in enumerate(setting.gold_first)])

    vocab = sorted(set(w for ws in req_words for w in ws) | set(w for ws in gold_words.values() for w in ws))
    enc = Encoder(word_encoder, device)
    # Words are embedded in alphabetical batches of 512: under left padding, batch composition causes small
    # numerical differences, and the 0.8 threshold of semantic coverage is sensitive to them.
    emb = torch.cat([word_embeddings(enc, vocab[k:k + batch_size], batch_size)
                     for k in range(0, len(vocab), batch_size)]).to(device)
    del enc
    torch.cuda.empty_cache()
    word_id = {w: i for i, w in enumerate(vocab)}
    semantic, soft = np.zeros(len(req_words)), np.zeros(len(req_words))
    for i, g in enumerate(setting.gold_first):
        sem, soft[i] = semantic_coverage(req_words[i], gold_words[g], emb, word_id, taus=(TAU,))
        semantic[i] = sem[TAU]
    return lexical, semantic, soft, len(vocab)


def background_gaps(setting, ft):
    """Gaps on the fine-tuned encoder with nothing added; a request whose top ten holds only gold
    documents gets a gap of 0."""
    b_d = document_background(ft.docs, 2)
    b_q = request_background(ft.pseudo[0], ft.queries)
    top = top_k(ft.queries, ft.docs, 10)
    return [np.nan_to_num(background_gap(ft.queries, ft.docs, b, top, setting.gold, setting.gold_first), nan=0.0)
            for b in (b_d, b_q)]


def dilution(setting, ft, section_encoder, device, batch_size):
    golds = sorted(set(setting.gold_first))
    texts, owner = [], []
    for g in golds:
        for s in split_sections(gold_body(setting.pool[g])):
            texts.append(s)
            owner.append(g)
    enc = Encoder(section_encoder, device)
    sections = enc(texts, batch_size).to(device)
    del enc
    torch.cuda.empty_cache()
    loss = dilution_loss(ft.queries, ft.docs, sections, owner, setting.gold_first)
    return loss, len(golds), len(texts)


def main():
    ap = argparse.ArgumentParser()
    add_setting_args(ap, encoders=("ft",))
    ap.add_argument("--word_encoder", default="Qwen/Qwen3-Embedding-0.6B", help="untuned encoder for word embeddings")
    ap.add_argument("--section_encoder", required=True, help="the fine-tuned encoder, to encode gold sections")
    ap.add_argument("--npz", required=True, help="output .npz with the per-request measures")
    ap.add_argument("--batch_size", type=int, default=16, help="batch size for encoding sections")
    a = ap.parse_args()

    setting = Setting(a.pool, a.requests)
    lexical, semantic, soft, n_vocab = coverages(setting, a.word_encoder, a.device, 512)
    print(f"coverage: vocabulary {n_vocab:,}; means {lexical.mean():.3f} / {semantic.mean():.3f} / {soft.mean():.3f}",
          flush=True)
    ft = Embeddings(setting, a.emb_ft, a.device)
    gap_bd, gap_bq = background_gaps(setting, ft)
    print(f"background gap: B_D {gap_bd.mean():.2f}, B_Q {gap_bq.mean():.2f}", flush=True)
    loss, n_docs, n_sections = dilution(setting, ft, a.section_encoder, a.device, a.batch_size)
    log_words = np.log1p([len(gold_body(setting.pool[g]).split()) for g in setting.gold_first])

    np.savez(a.npz, lexical=lexical, semantic=semantic, soft=soft, gap_bd=gap_bd, gap_bq=gap_bq,
             dilution=loss, log_words=log_words)
    summary = {"n_requests": len(setting.reqs), "n_vocabulary": n_vocab, "n_gold_docs": n_docs,
               "n_sections": n_sections,
               "mean": {k: float(v.mean()) for k, v in (("lexical", lexical), ("semantic", semantic), ("soft", soft),
                                                         ("gap_bd", gap_bd), ("gap_bq", gap_bq),
                                                         ("dilution", loss))},
               "median_dilution": float(np.median(loss))}
    print(json.dumps(summary, indent=1))
    with open(a.out, "w") as f:
        json.dump(summary, f, indent=1)


if __name__ == "__main__":
    main()
