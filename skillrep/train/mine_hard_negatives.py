#!/usr/bin/env python3
"""Step 2: mine up to ten negatives per training request with the untuned encoder.

Skills ("name | description[:500] | body[:8000]") and requests (training instruction + request[:2000])
are encoded with the untuned Qwen3-Embedding-0.6B. For each request, the top 50 skills are scanned in
order and the first seven that pass three false-negative filters become hard negatives:
  - trigram Jaccard similarity with the positive's body (first 3,000 characters) at most 0.6;
  - cosine similarity with the positive skill at most 0.92;
  - no shared five-character name prefix with the positive (names longer than five characters).
Three random skills from outside the top 50 complete the ten.

Output: JSONL {"task", "query", "positive": pid, "negatives": [pid, ...]}.

Example:
  python train/mine_hard_negatives.py --pool data/train_pool.jsonl --requests data/train_requests.jsonl \
      --model Qwen/Qwen3-Embedding-0.6B --out data/train_mined.jsonl --device cuda:0
"""
import argparse
import json
import os
import random
import sys

import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import TRAIN_INSTRUCTION, format_skill, load_jsonl, skill_id  # noqa: E402
from skillrep.encoder import Encoder  # noqa: E402


def trigrams(text, k=3):
    toks = text.lower().split()
    if len(toks) < k:
        return {" ".join(toks)} if toks else set()
    return {" ".join(toks[i:i + k]) for i in range(len(toks) - k + 1)}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    i = len(a & b)
    return i / (len(a) + len(b) - i)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True, help="training skill pool (JSONL)")
    ap.add_argument("--requests", required=True, help="output of synthesize_requests.py")
    ap.add_argument("--model", default="Qwen/Qwen3-Embedding-0.6B")
    ap.add_argument("--out", required=True)
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--batch_size", type=int, default=16)
    ap.add_argument("--max_length", type=int, default=4096)
    ap.add_argument("--top_k", type=int, default=50)
    ap.add_argument("--num_hard", type=int, default=7)
    ap.add_argument("--num_random", type=int, default=3)
    ap.add_argument("--jaccard_threshold", type=float, default=0.6)
    ap.add_argument("--cosine_threshold", type=float, default=0.92)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    random.seed(a.seed)

    skills, reqs = load_jsonl(a.pool), load_jsonl(a.requests)
    index = {skill_id(s): i for i, s in enumerate(skills)}
    enc = Encoder(a.model, a.device, a.max_length)
    skill_emb = enc([format_skill(s) for s in skills], a.batch_size).to(a.device)
    query_emb = enc([TRAIN_INSTRUCTION + r["query"][:2000] for r in reqs], a.batch_size).to(a.device)
    del enc
    torch.cuda.empty_cache()
    shingles = [trigrams((s.get("body") or "")[:3000]) for s in skills]

    n_written = 0
    with open(a.out, "w", encoding="utf-8") as fout:
        for start in range(0, len(reqs), 1024):
            top = (query_emb[start:start + 1024] @ skill_emb.T).topk(min(a.top_k, len(skills)), dim=1).indices.cpu()
            for r, cand in zip(reqs[start:start + 1024], top.tolist()):
                pos = index.get(str(r["gt_pids"][0]))
                if pos is None:
                    continue
                pos_name = skills[pos].get("name") or ""
                hard = []
                for c in cand:
                    if c == pos or jaccard(shingles[pos], shingles[c]) > a.jaccard_threshold:
                        continue
                    if float(skill_emb[c] @ skill_emb[pos]) > a.cosine_threshold:
                        continue
                    name = skills[c].get("name") or ""
                    if len(pos_name) > 5 and len(name) > 5 and pos_name[:5].lower() == name[:5].lower():
                        continue
                    hard.append(c)
                    if len(hard) >= a.num_hard:
                        break
                excluded = set(cand) | {pos}
                rand = []
                while len(rand) < a.num_random and len(excluded) < len(skills):
                    j = random.randrange(len(skills))
                    if j not in excluded:
                        excluded.add(j)
                        rand.append(j)
                negs = (hard + rand)[:a.num_hard + a.num_random]
                fout.write(json.dumps({"task": r["task"], "query": r["query"], "positive": skill_id(skills[pos]),
                                       "negatives": [skill_id(skills[j]) for j in negs]}, ensure_ascii=False) + "\n")
                n_written += 1
    print(f"{n_written} requests with negatives -> {a.out}")


if __name__ == "__main__":
    main()
