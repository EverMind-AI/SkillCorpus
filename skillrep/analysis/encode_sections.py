#!/usr/bin/env python3
"""Encode skill sections for the dilution analyses.

--mode index  every pool body cut by dilution_utils.index_sections, each section encoded as
              "name | description[:300] | section" with a limit of 1,024 tokens: the index of the
              section-level retriever (dilution.py, padding.py).
--mode gold   the first gold document of every request, cut by skillrep.measures.split_sections and
              encoded without a prefix (limit 4,096 tokens): the sections s of the dilution loss
              l(q) = cos(q, s*) - cos(q, d).
Output: a torch file {"emb": [M, d], "owner": LongTensor[M] (pool index), "texts": [...] (gold mode)}.

Example:
  python analysis/encode_sections.py --mode index --model EverMind-AI/skillrep-embedding-0.6b \
      --pool data/unipool/pool.jsonl --out emb/unipool_sra_ft/sections.pt
  python analysis/encode_sections.py --mode gold --model EverMind-AI/skillrep-embedding-0.6b \
      --pool data/unipool/pool.jsonl --requests data/sra/requests.jsonl --out emb/unipool_sra_ft/gold_sections.pt
"""
import argparse
import os
import sys

import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from skillrep.data import load_jsonl  # noqa: E402
from skillrep.encoder import Encoder  # noqa: E402
from skillrep.measures import gold_body, split_sections  # noqa: E402
from dilution_utils import INDEX_MAX_LENGTH, index_sections, section_prefix  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["index", "gold"], required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--pool", required=True)
    ap.add_argument("--requests", help="gold mode: requests.jsonl")
    ap.add_argument("--out", required=True)
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--batch_size", type=int, default=16)
    ap.add_argument("--max_docs", type=int, default=0, help="index mode: encode only the first n skills (for checks)")
    a = ap.parse_args()

    pool = load_jsonl(a.pool)
    texts, owner = [], []
    if a.mode == "index":
        for i, s in enumerate(pool[:a.max_docs or None]):
            head = section_prefix(s)
            for c in index_sections(s.get("body")):
                texts.append(head + c)
                owner.append(i)
        max_length = INDEX_MAX_LENGTH
    else:
        pid2i = {s["pid"]: i for i, s in enumerate(pool)}
        golds = sorted({pid2i[r["gt_pids"][0]] for r in load_jsonl(a.requests)})
        for g in golds:
            for t in split_sections(gold_body(pool[g])):
                texts.append(t)
                owner.append(g)
        max_length = 4096
    print(f"{len(texts):,} sections from {len(set(owner)):,} skills", flush=True)
    enc = Encoder(a.model, a.device)
    out = {"emb": enc(texts, a.batch_size, max_length), "owner": torch.tensor(owner, dtype=torch.long)}
    if a.mode == "gold":
        out["texts"] = texts
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    torch.save(out, a.out)
    print(f"wrote {a.out}: {tuple(out['emb'].shape)}")


if __name__ == "__main__":
    main()
