#!/usr/bin/env python3
"""How the lexical channel recovers what dilution costs.

The lexical channel (BM25 with the request alone, score fusion at alpha = 0.3) is added to the fine-tuned
encoder by itself.

Part 1, shared words. For requests whose gold document the channel moves into the top ten, and those whose
gold document it pushes out, the mean of
  (content words a request shares with its gold document) - (those it shares with its strongest dense
  competitor, the first non-gold document of the dense ranking),
over all shared words and over the halves and quarters of the shared words split by document frequency.

Part 2, deleting shared words (needs --model). For each request and its first gold document d, S is the set
of content words the two share, and C an equally large control set of other words of d, each matched to a
word of S by IDF and number of occurrences. Deleting all occurrences of S (or C) from d and re-encoding it
(limit 4,096 tokens) gives the drop in cos(q, d) (doc_S, doc_C). Deleting them only inside s*, the section
of d most similar to q (sections cut at headings, at least 200 characters, encoded on their own), gives the
drop in the section's similarity (sec_S, sec_C) and, with the edited section put back, in the whole
document's (docin_S, docin_C). Per-request results are appended to <out>.jsonl so that an interrupted run
resumes.

Example:
  python analysis/lexical_mechanism.py --pool data/unipool/pool.jsonl --requests data/sra/requests.jsonl \
      --emb_ft emb/unipool_sra_ft --model EverMind-AI/skillrep-embedding-0.6b --out results/lexical_mechanism.json
"""
import argparse
import json
import math
import os
import re
from collections import Counter

import numpy as np
import torch
import torch.nn.functional as F

from common import Setting, dense_topn  # also puts the package root on sys.path
from dilution_utils import heading_sections
from skillrep.data import load_embeddings
from skillrep.encoder import Encoder
from skillrep.fusion import score_fusion
from skillrep.measures import CONTENT_WORD, content_words, gold_body

ALPHA, CHUNK = 0.3, 160
DELTAS = ("d_doc_S", "d_doc_C", "d_sec_S", "d_sec_C", "d_docin_S", "d_docin_C")


def gold_ranks(st, queries, docs):
    """Rank (1-10, or 99) of the gold document without and with the lexical channel, and each request's
    strongest dense competitor."""
    d_ids, d_sc = dense_topn(queries, docs)
    l_ids, l_sc = st.lexical_hits(False)
    without, with_lex = np.full(len(st.reqs), 99), np.full(len(st.reqs), 99)
    comp = np.zeros(len(st.reqs), dtype=np.int64)
    for i in range(len(st.reqs)):
        comp[i] = next(int(j) for j in d_ids[i] if int(j) not in st.gold[i])
        for ranks, order in ((without, score_fusion(d_ids[i], d_sc[i])),
                             (with_lex, score_fusion(d_ids[i], d_sc[i], l_ids[i], l_sc[i], ALPHA))):
            for r, j in enumerate(order[:10]):
                if j in st.gold[i]:
                    ranks[i] = r + 1
                    break
    return without, with_lex, comp


def shared_words(st, r0, r1, comp, doc_words, df):
    qw = [set(content_words(r["query"])) for r in st.reqs]
    g0 = st.gold_first
    shared_df = np.array([df[w] for i in range(len(qw)) for w in qw[i] & doc_words[g0[i]]])
    q25, q50, q75 = np.percentile(shared_df, [25, 50, 75])

    def net(i, lo, hi):
        a = sum(1 for w in qw[i] & doc_words[g0[i]] if lo <= df[w] < hi)
        b = sum(1 for w in qw[i] & doc_words[comp[i]] if lo <= df[w] < hi)
        return a - b
    bands = {"all": (0, 10 ** 9), "rare_half": (0, q50), "common_half": (q50, 10 ** 9),
             "rarest_quarter": (0, q25), "commonest_quarter": (q75, 10 ** 9)}
    groups = {"into_top10": np.nonzero((r0 > 10) & (r1 <= 10))[0], "out_of_top10": np.nonzero((r0 <= 10) & (r1 > 10))[0],
              "rank_up": np.nonzero(r1 < r0)[0], "rank_down": np.nonzero(r1 > r0)[0]}
    out = {"shared_df_quartiles": [float(q25), float(q50), float(q75)], "groups": {}}
    for name, ix in groups.items():
        out["groups"][name] = {"n": int(len(ix)), **{b: float(np.mean([net(i, *lh) for i in ix])) for b, lh in bands.items()}}
    return out


# ---------------------------------------------------------------- part 2

def word_counts(text):
    return Counter(CONTENT_WORD.findall(text.lower()))


def delete_words(text, words):
    """Deletes every whole-word occurrence (case-insensitive) and collapses the spaces left behind."""
    if not words:
        return text
    pat = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(sorted(map(re.escape, words), key=len, reverse=True))
                     + r")(?![A-Za-z0-9_])", re.I)
    return re.sub(r"[ \t]{2,}", " ", pat.sub("", text))


def matched_controls(targets, text, request_words, idf):
    """For each target word (most frequent first), the closest unused word of the text that the request
    does not contain, by IDF and log number of occurrences."""
    counts = word_counts(text)
    cand = sorted(w for w in set(content_words(text)) if w not in request_words)
    used, out = set(), []
    for w in sorted(targets, key=lambda x: -counts[x]):
        best, bd = None, 1e9
        for c in cand:
            if c in used:
                continue
            d = abs(idf.get(c, 0) - idf.get(w, 0)) / 0.5 + abs(math.log1p(counts[c]) - math.log1p(counts[w])) / 0.3
            if d < bd:
                best, bd = c, d
        if best is not None:
            used.add(best)
            out.append(best)
    return out


def word_stats(words, text, idf):
    counts = word_counts(text)
    return {"n": len(words), "occ": sum(counts[w] for w in words),
            "idf": float(np.mean([idf.get(w, 0) for w in words])) if words else None}


def deletion_probe(st, queries, r0, r1, idf, enc, work_path, batch_size):
    rr = lambda r: np.where(r <= 10, 1.0 / r, 0.0)
    group = np.where(rr(r1) > rr(r0), "rescued", np.where(rr(r1) < rr(r0), "hurt", "flat"))
    g0 = st.gold_first
    golds = sorted(set(g0))
    gi = {g: k for k, g in enumerate(golds)}
    body = lambda g: gold_body(st.pool[g])
    gold_vec = enc([body(g) for g in golds], batch_size).to(queries.device)
    sec_txt = [t for g in golds for t in heading_sections(body(g))]
    sec_of = np.array([g for g in golds for _ in heading_sections(body(g))])
    sec_vec = enc(sec_txt, batch_size).to(queries.device)

    done = set()
    if os.path.exists(work_path):
        with open(work_path) as f:
            done = {json.loads(line)["qi"] for line in f}
    todo = [i for i in range(len(st.reqs)) if i not in done]
    for s0 in range(0, len(todo), CHUNK):
        jobs, recs = [], []
        for qi in todo[s0:s0 + CHUNK]:
            g, q = g0[qi], queries[qi]
            t = body(g)
            qw = set(content_words(st.reqs[qi]["query"]))
            shared = sorted(qw & set(content_words(t)))
            r = {"qi": qi, "group": str(group[qi]), "nS": len(shared), "cos_doc": float(gold_vec[gi[g]] @ q)}
            m = np.nonzero(sec_of == g)[0]
            s_star = None
            if len(m):
                sims = (sec_vec[torch.from_numpy(m).to(q.device)] @ q).cpu().numpy()
                s_star = sec_txt[int(m[sims.argmax()])]
                r["cos_sec"] = float(sims.max())
                r["ell"] = r["cos_sec"] - r["cos_doc"]
            if shared:
                ctrl = matched_controls(shared, t, qw, idf)
                r["S"], r["C"] = word_stats(shared, t, idf), word_stats(ctrl, t, idf)
                jobs += [(qi, "doc_S", delete_words(t, shared)), (qi, "doc_C", delete_words(t, ctrl))]
                if s_star is not None:
                    counts = word_counts(s_star)
                    shared_s = [w for w in shared if counts[w]]
                    if shared_s:
                        ctrl_s = matched_controls(shared_s, s_star, qw, idf)
                        r["Ss"], r["Cs"] = word_stats(shared_s, s_star, idf), word_stats(ctrl_s, s_star, idf)
                        for tag, ws in (("S", shared_s), ("C", ctrl_s)):
                            edited = delete_words(s_star, ws)
                            jobs += [(qi, f"sec_{tag}", edited), (qi, f"docin_{tag}", t.replace(s_star, edited, 1))]
            recs.append(r)
        by = {r["qi"]: r for r in recs}
        if jobs:
            vec = enc([j[2] for j in jobs], batch_size).to(queries.device)
            for (qi, tag, _), v in zip(jobs, vec):
                r = by[qi]
                r["d_" + tag] = (r["cos_sec"] if tag.startswith("sec") else r["cos_doc"]) - float(v @ queries[qi])
        with open(work_path, "a") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        print(f"  deletion probe {s0 + len(recs)}/{len(todo)}", flush=True)

    with open(work_path) as f:
        rows = [json.loads(line) for line in f]
    out = {"n_requests": len(rows),
           "groups": {k: int(sum(r["group"] == k for r in rows)) for k in ("rescued", "hurt", "flat")}}
    for name, sub in (("all", rows), ("rescued", [r for r in rows if r["group"] == "rescued"]),
                      ("hurt", [r for r in rows if r["group"] == "hurt"])):
        o = {}
        for key in DELTAS:
            v = [r[key] for r in sub if key in r]
            o[key] = {"mean": float(np.mean(v)) if v else None, "n": len(v)}
        for key in ("S", "C", "Ss", "Cs"):
            v = [r[key] for r in sub if key in r]
            if v:
                o[key] = {"n": float(np.mean([x["n"] for x in v])), "occ": float(np.mean([x["occ"] for x in v])),
                          "idf": float(np.nanmean([x["idf"] for x in v if x["idf"] is not None]))}
        out[name] = o
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True)
    ap.add_argument("--requests", required=True)
    ap.add_argument("--emb_ft", required=True, help="scripts/encode.py output for the fine-tuned encoder")
    ap.add_argument("--model", help="the fine-tuned encoder; part 2 is skipped without it")
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--batch_size", type=int, default=8)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    st = Setting(a.pool, a.requests)
    emb = load_embeddings(a.emb_ft, st.reqs, st.pool)
    docs = F.normalize(emb["docs"], dim=-1).to(a.device)
    queries = F.normalize(emb["requests"], dim=-1).to(a.device)
    r0, r1, comp = gold_ranks(st, queries, docs)
    doc_words = [set(content_words(gold_body(s))) for s in st.pool]
    df = Counter()
    for w in doc_words:
        df.update(w)
    res = {"shared_words": shared_words(st, r0, r1, comp, doc_words, df)}
    for name, g in res["shared_words"]["groups"].items():
        print(f"{name:<14} n={g['n']:<5} all {g['all']:+.2f}  rare half {g['rare_half']:+.2f}  common half {g['common_half']:+.2f}")
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    if a.model:
        idf = {w: math.log(len(st.pool) / (1 + c)) for w, c in df.items()}
        enc = Encoder(a.model, a.device)
        res["deletion"] = deletion_probe(st, queries, r0, r1, idf, enc, os.path.splitext(a.out)[0] + ".jsonl", a.batch_size)
        print("mean drop in similarity (all requests)")
        for key in DELTAS:
            print(f"  {key:<10} {res['deletion']['all'][key]['mean']:+.4f}  (n={res['deletion']['all'][key]['n']})")
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()
