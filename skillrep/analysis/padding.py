#!/usr/bin/env python3
"""Padding the gold documents with content the requests do not need.

Only the gold documents change; the requests stay as they are. L sections are appended to every gold body
(the first 8,000 characters), of three kinds:
  other    sections drawn at random from other (non-gold) skills
  generic  of 20,000 sections drawn from other skills, the 2,000 closest to their mean (the library's
           most template-like sections), L drawn per gold document without replacement
  own      L copies of the gold document's own section least similar to its requests
Sections are cut at headings and kept if at least 200 characters long. Padded bodies are re-encoded with
a limit of 4,096 tokens (also at L = 0). Three retrievers then search separately:
  one vector  the library's body vectors, the gold ones replaced by the re-encoded bodies
  sections    the section index (encode_sections.py --mode index), the appended sections encoded in the
              same format and added to their gold document
  BM25        the request alone, the index rebuilt on the padded bodies
Reported per kind and L: the mean dilution loss of the gold documents (their own sections plus the
appended ones, encoded without a prefix), each retriever's macro MRR@10 and its change from L = 0 with a
95% paired bootstrap interval stratified by source dataset; also the share of gold documents that
exceed 4,096 tokens at L = 8.

Example:
  python analysis/padding.py --model path/to/finetuned-encoder --pool data/unipool/pool.jsonl \
      --requests data/sra/requests.jsonl --emb_ft emb/unipool_sra_ft --sections emb/unipool_sra_ft/sections.pt \
      --out results/padding.json
"""
import argparse
import json
import os

import numpy as np
import torch
import torch.nn.functional as F

from common import Setting  # also puts the package root on sys.path
from dilution_utils import (best_section_scores, bm25_doc_text, bm25_top, heading_sections, load_section_index,
                            rr_from_top, section_prefix)
from skillrep.data import load_embeddings
from skillrep.encoder import Encoder
from skillrep.metrics import paired_bootstrap

BODY_MAX, MAX_TOKENS = 8000, 4096
PAD_MAX = 8              # sections drawn per gold document; levels use the first L of them
N_GENERIC_POOL, N_GENERIC = 20000, 2000


def draw_padding(st, enc, queries, golds, batch_size):
    """The three kinds of padding, PAD_MAX sections per gold document, and the gold documents' own sections."""
    n = len(st.pool)
    body = lambda i: (st.pool[i].get("body") or "")[:BODY_MAX]
    gold_set = set(golds)

    def sample_sections(rng, stop):
        """Sections of non-gold skills, visited in a random order, until at least `stop` are collected."""
        out = []
        for i in rng.permutation(n):
            if int(i) in gold_set:
                continue
            out += heading_sections(body(int(i)))
            if len(out) >= stop:
                break
        return out

    # other: sections of randomly ordered skills, shuffled again
    rng = np.random.default_rng(0)
    filler = sample_sections(rng, PAD_MAX * len(golds) * 2)
    filler = [filler[k] for k in rng.permutation(len(filler))[:PAD_MAX * len(golds)]]
    other = {g: filler[k * PAD_MAX:(k + 1) * PAD_MAX] for k, g in enumerate(golds)}

    # own: the gold document's section least similar, on average, to the requests it serves
    own = {g: heading_sections(body(g)) or [body(g)] for g in golds}
    own_txt = [t for g in golds for t in own[g]]
    own_of = np.array([g for g in golds for _ in own[g]])
    own_emb = enc(own_txt, batch_size).to(queries.device)
    least = {}
    for g in golds:
        m = np.nonzero(own_of == g)[0]
        served = [i for i in range(len(st.reqs)) if g in st.gold[i]]
        least[g] = own[g][int((own_emb[m] @ queries[served].T).mean(1).cpu().numpy().argmin())]
    self_pad = {g: [least[g]] * PAD_MAX for g in golds}

    # generic: sections closest to the mean of a large random sample of other skills' sections
    cand = sample_sections(np.random.default_rng(1), N_GENERIC_POOL)[:N_GENERIC_POOL]
    cand_emb = enc(cand, batch_size).to(queries.device)
    center = F.normalize(cand_emb.mean(0, keepdim=True), dim=-1)
    central = (cand_emb @ center.T).squeeze(1).topk(N_GENERIC).indices.cpu().numpy()
    rg = np.random.default_rng(2)
    generic = {g: [cand[int(k)] for k in rg.choice(central, PAD_MAX, replace=False)] for g in golds}
    centrality = {"generic": float((cand_emb[torch.as_tensor(central, device=cand_emb.device)] @ center.T).mean()),
                  "random": float((cand_emb @ center.T).mean())}
    return {"other": other, "generic": generic, "own": self_pad}, own_emb, own_of, centrality


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="the fine-tuned encoder (re-encodes the padded bodies)")
    ap.add_argument("--pool", required=True)
    ap.add_argument("--requests", required=True)
    ap.add_argument("--emb_ft", required=True, help="scripts/encode.py output for the fine-tuned encoder")
    ap.add_argument("--sections", required=True, help="encode_sections.py --mode index output")
    ap.add_argument("--kinds", nargs="+", default=["other", "generic", "own"])
    ap.add_argument("--levels", nargs="+", type=int, default=[2, 8], help=f"sections appended (at most {PAD_MAX})")
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--batch_size", type=int, default=8)
    ap.add_argument("--n_boot", type=int, default=5000)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    assert max(a.levels) <= PAD_MAX

    st = Setting(a.pool, a.requests)
    emb = load_embeddings(a.emb_ft, st.reqs, st.pool)
    docs = F.normalize(emb["docs"], dim=-1).to(a.device)
    queries = F.normalize(emb["requests"], dim=-1).to(a.device)
    sec_emb, sec_owner = load_section_index(a.sections, a.device, half=True)
    n_req = queries.shape[0]
    golds = sorted(set().union(*st.gold))
    gi = {g: k for k, g in enumerate(golds)}
    gold_idx = torch.tensor(golds, device=a.device)
    body = lambda i: (st.pool[i].get("body") or "")[:BODY_MAX]
    enc = Encoder(a.model, a.device, max_length=MAX_TOKENS)
    pads, own_emb, own_of, centrality = draw_padding(st, enc, queries, golds, a.batch_size)
    print(f"{len(golds)} gold documents", flush=True)

    n_tokens = lambda t: len(enc.tok(t, truncation=False)["input_ids"])
    res = {"truncated_at_max_level": {k: float(np.mean([
        n_tokens(body(g) + "".join("\n\n" + t for t in pads[k][g][:max(a.levels)])) > MAX_TOKENS for g in golds]))
        for k in a.kinds}, "generic_centrality": centrality, "levels": {}}

    def evaluate(pad, level):
        texts = {g: body(g) + "".join("\n\n" + t for t in pad[g][:level]) for g in golds}
        gold_vec = enc([texts[g] for g in golds], 4).to(a.device)
        d = docs.clone()
        d[gold_idx] = gold_vec
        # dilution loss of the first gold document: its own sections plus the appended ones
        pad_emb = enc([t for g in golds for t in pad[g][:level]], a.batch_size).to(a.device) if level else None
        loss = np.zeros(n_req)
        for i in range(n_req):
            g = st.gold_first[i]
            best = (own_emb[np.nonzero(own_of == g)[0]] @ queries[i]).max()
            if level:
                best = torch.maximum(best, (pad_emb[gi[g] * level:(gi[g] + 1) * level] @ queries[i]).max())
            loss[i] = float(best - gold_vec[gi[g]] @ queries[i])
        # appended sections in the section index's format
        pad_sec = (enc([section_prefix(st.pool[g]) + t for g in golds for t in pad[g][:level]], a.batch_size)
                   .to(a.device).half() if level else None)
        t1, t2 = [], []
        with torch.no_grad():
            for s in range(0, n_req, 64):
                q = queries[s:s + 64]
                t1 += (q @ d.T).topk(10, dim=1).indices.cpu().tolist()
                sc = best_section_scores(q, sec_emb, sec_owner, docs)
                if level:
                    p = (q.half() @ pad_sec.T).float().view(q.shape[0], len(golds), level).max(2).values
                    sc[:, gold_idx] = torch.maximum(sc[:, gold_idx], p)
                t2 += sc.topk(10, dim=1).indices.cpu().tolist()
        bm = bm25_top([bm25_doc_text(s, texts.get(k)) for k, s in enumerate(st.pool)], [r["query"] for r in st.reqs])
        rr = {"one_vector": rr_from_top(t1, st.gold), "sections": rr_from_top(t2, st.gold), "bm25": rr_from_top(bm, st.gold)}
        row = {"words_added": float(np.mean([sum(len(t.split()) for t in pad[g][:level]) for g in golds])),
               "dilution_loss": float(loss.mean()), **{k: st.macro(v) * 100 for k, v in rr.items()}}
        return row, rr

    base_row, base_rr = evaluate(pads["other"], 0)
    res["levels"]["none|0"] = base_row
    print(f"none     L=0  loss {base_row['dilution_loss']:.4f}  " +
          "  ".join(f"{k} {base_row[k]:.2f}" for k in base_rr), flush=True)
    for kind in a.kinds:
        for level in a.levels:
            row, rr = evaluate(pads[kind], level)
            for k in rr:
                delta, ci = paired_bootstrap(rr[k] * 100, base_rr[k] * 100, st.groups, a.n_boot)
                row[f"{k}_delta"] = {"delta": delta, "ci": ci}
            res["levels"][f"{kind}|{level}"] = row
            print(f"{kind:<8} L={level}  loss {row['dilution_loss']:.4f}  " +
                  "  ".join(f"{k} {row[k + '_delta']['delta']:+.2f}" for k in rr), flush=True)
            os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
            with open(a.out, "w") as f:
                json.dump(res, f, indent=1)
    print("share of gold documents over the token limit at the largest level", res["truncated_at_max_level"])
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()
