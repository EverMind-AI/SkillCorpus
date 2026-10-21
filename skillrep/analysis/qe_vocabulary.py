#!/usr/bin/env python3
"""How query-side expansion supplies the unsaid vocabulary (Section 7.2, last paragraph).

The hypothetical skills of each request are edited and re-encoded with the fine-tuned encoder, and
query-side expansion (w = 0.3, dense channel only, on the fine-tuned encoder alone) is rerun:
  original       the generated texts
  drop_gold      the gold-document words the request lacks are deleted ("new" words: in the first
                 8,000 characters of the gold body, not in the request)
  drop_ctrl_idf  as many other words are deleted (in neither the gold body nor the request), each
                 matched to a deleted word by IDF over the library's bodies
  keep_gold      only the new words are kept
Reported: each variant's MRR@10 gain over the bare encoder and its change against the original
texts, with 95% intervals from a bootstrap over gold-skill clusters (macro over source datasets).
The paper reports -3.12 (drop_gold), -0.08 (drop_ctrl_idf) and +4.27 (keep_gold). The control words
of drop_ctrl_idf are drawn at random among equally good IDF matches (--seed), so that variant has
some randomness and its value varies slightly between seeds.

Example:
  python analysis/qe_vocabulary.py --pool data/unipool/pool.jsonl --requests data/sra/requests.jsonl \
      --hypothetical data/sra/hypothetical_skills.json --emb_ft emb/unipool_sra_ft \
      --encoder path/to/finetuned-encoder --out results/qe_vocabulary.json
"""
import argparse
import json
import math
import re
from collections import Counter

import numpy as np
import torch

from common import W, Embeddings, Setting, add_setting_args, rr_from_rankings
from skillrep.encoder import Encoder
from skillrep.pipeline import mix
from skillrep.measures import CONTENT_STOP, GOLD_BODY_MAX

# Matched on the original text (the edits keep its case); words are compared in lower case.
TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")


def words(text):
    return {w for w in (m.lower() for m in TOKEN.findall(text or "")) if w not in CONTENT_STOP}


def edit(text, drop=None, keep=None):
    if keep is not None:
        return " ".join(m for m in TOKEN.findall(text) if m.lower() in keep)
    return re.sub(r"[ \t]{2,}", " ", TOKEN.sub(lambda m: "" if m.group(0).lower() in drop else m.group(0), text))


def build_variants(setting, seed=0):
    """Returns ({variant: texts}, owner list, matching statistics)."""
    pool = setting.pool
    df = Counter()
    for s in pool:
        df.update(words((s.get("body") or "")[:GOLD_BODY_MAX]))
    n = len(pool)
    idf = lambda w: math.log((n + 1) / (df.get(w, 0) + 1))
    rng = np.random.default_rng(seed)
    texts = {k: [] for k in ("original", "drop_gold", "drop_ctrl_idf", "keep_gold")}
    owner, stats = [], []
    for i, r in enumerate(setting.reqs):
        gold_words = words((pool[setting.gold_first[i]].get("body") or "")[:GOLD_BODY_MAX])
        req_words = words(r["query"])
        for t in setting.hyp_texts.get(r["task"], []):
            cnt = Counter(m.lower() for m in TOKEN.findall(t))
            new = [w for w in cnt if w in gold_words and w not in req_words and w not in CONTENT_STOP]
            other = [w for w in cnt if w not in gold_words and w not in req_words and w not in CONTENT_STOP]
            rng.shuffle(other)
            ctrl = set()
            # most frequent new words are matched first; ties keep their order of first occurrence
            for w in sorted(new, key=lambda x: -cnt[x]):
                cand = [o for o in other if o not in ctrl]
                if cand:
                    ctrl.add(min(cand, key=lambda o: abs(idf(o) - idf(w))))
            new = set(new)
            owner.append(i)
            texts["original"].append(t)
            texts["drop_gold"].append(edit(t, drop=new))
            texts["drop_ctrl_idf"].append(edit(t, drop=ctrl))
            texts["keep_gold"].append(edit(t, keep=new) or " ")
            stats.append((sum(cnt[w] for w in new), sum(cnt[w] for w in ctrl),
                          np.mean([idf(w) for w in new]) if new else np.nan,
                          np.mean([idf(w) for w in ctrl]) if ctrl else np.nan, len(new), len(ctrl)))
    st = np.array(stats, dtype=float)
    match = dict(occurrences_new=float(np.nansum(st[:, 0])), occurrences_ctrl=float(np.nansum(st[:, 1])),
                 idf_new=float(np.nanmean(st[:, 2])), idf_ctrl=float(np.nanmean(st[:, 3])),
                 types_new=float(st[:, 4].sum()), types_ctrl=float(st[:, 5].sum()))
    return texts, owner, match


def cluster_ci(x, setting, n=5000, seed=0):
    """95% interval of the macro mean (x 100), resampling requests by their first gold skill."""
    ds = np.asarray(setting.groups)
    cl = {}
    for i, g in enumerate(setting.gold_first):
        cl.setdefault(g, []).append(i)
    clusters = [np.array(v) for v in cl.values()]
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        ix = np.concatenate([clusters[j] for j in rng.integers(0, len(clusters), len(clusters))])
        out.append(np.mean([x[ix][ds[ix] == d].mean() for d in sorted(set(ds)) if (ds[ix] == d).any()]) * 100)
    return [float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))]


def main():
    ap = argparse.ArgumentParser()
    add_setting_args(ap)
    ap.add_argument("--encoder", required=True, help="fine-tuned encoder checkpoint")
    ap.add_argument("--batch_size", type=int, default=16)
    ap.add_argument("--n_boot", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=0, help="seed for drawing the control words")
    a = ap.parse_args()

    s = Setting(a.pool, a.requests, a.hypothetical)
    e = Embeddings(s, a.emb_ft, a.device)
    texts, owner, match = build_variants(s, a.seed)
    print("matching", json.dumps(match), flush=True)
    owner = torch.tensor(owner, device=a.device)

    def rr_of(q):
        top = torch.cat([(q[k:k + 256] @ e.docs.T).topk(10, dim=1).indices.cpu()
                         for k in range(0, q.shape[0], 256)]).tolist()
        return rr_from_rankings(top, s.gold)

    bare = rr_of(e.queries)
    res = {"bare": s.macro(bare) * 100, "match": match, "variants": {}}
    enc = Encoder(a.encoder, a.device)
    rr = {}
    for name, tx in texts.items():
        emb = enc(tx, batch_size=a.batch_size).to(a.device)
        if name == "original" and e.hypothetical is not None and e.hypothetical[0].shape[0] == emb.shape[0]:
            res["cosine_with_given_embeddings"] = float((emb * e.hypothetical[0]).sum(1).mean())
        q = mix(e.queries, emb, owner, W)  # mean of each request's edited texts, weight w
        rr[name] = rr_of(q)
        gain = rr[name] - bare
        row = {"gain": s.macro(gain) * 100, "gain_ci": cluster_ci(gain, s, a.n_boot)}
        if name != "original":
            d = rr[name] - rr["original"]
            row["vs_original"] = {"delta": s.macro(d) * 100, "ci": cluster_ci(d, s, a.n_boot)}
        res["variants"][name] = row
        print(name, json.dumps(row), flush=True)
        with open(a.out, "w") as f:
            json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()
