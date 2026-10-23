# SPDX-License-Identifier: MIT
"""Recompute the unaided (U) and aided (A) capability scores and the headline audit numbers from the item-level files.

Usage: python reproduce_labels.py path/to/dataset/scores

The dataset is the paper's dataset (see ../README.md); this script needs only the Python 3 standard
library.

U and A are means over capability items of task_completion item scores (score / max_points), averaged over runs.
The target model's unaided arm is distributed as three-run item means.
A task passes the validity gate for a target model when some other model gains more than 0.1 from the skill.
"""
import csv, gzip, sys, statistics as st
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

if len(sys.argv) != 2:
    sys.exit("usage: python reproduce_labels.py path/to/dataset/scores")
D = Path(sys.argv[1])
TARGET = "Qwen3.6-27B"
MODELS = [TARGET, "Qwen3.5-397B", "DeepSeek-V4-Flash", "Qwen3.8-27B"]

cap = defaultdict(set)
for r in csv.DictReader(open(D / "items.csv", encoding="utf-8")):
    if r["is_capability_item"] == "1":
        cap[r["task_id"]].add(r["item"])

runs = defaultdict(dict)  # (task, model, arm, run) -> {item: fraction}
with gzip.open(D / "item_runs.csv.gz", "rt") as f:
    for r in csv.DictReader(f):
        if r["rubric"] != "task_completion":
            continue
        mx = float(r["max_points"])
        runs[(r["task_id"], r["model"], r["arm"], int(r["run"]))][r["item"]] = float(r["score"]) / mx if mx else 0.0
means = defaultdict(dict)
for r in csv.DictReader(open(D / "target_unaided_item_means.csv", encoding="utf-8")):
    means[r["task_id"]][r["item"]] = float(Fraction(r["mean_exact"]))

idx = defaultdict(list)
for (t, m, a, k), sc in runs.items():
    idx[(t, m, a)].append(sc)
def arm_score(task, model, arm):
    if model == TARGET and arm == "unaided":
        v = [means[task][i] for i in cap[task] if i in means[task]]
        return st.mean(v) if v else None
    per = [st.mean([sc[i] for i in cap[task] if i in sc]) for sc in idx[(task, model, arm)] if any(i in sc for i in cap[task])]
    return st.mean(per) if per else None

rows = []
for r in csv.DictReader(open(D / "skills.csv", encoding="utf-8")):
    t = r["task_id"]
    if not cap[t]:
        continue
    sc = {m: (arm_score(t, m, "unaided"), arm_score(t, m, "aided")) for m in MODELS}
    if any(u is None or a is None for u, a in sc.values()):
        continue
    rows.append((r, sc))

def check(r, sc):  # agreement with the distributed per-skill scores
    return max(abs(sc[m][0] - float(r[f"U_{m}"])) for m in MODELS) + max(abs(sc[m][1] - float(r[f"A_{m}"])) for m in MODELS)
print("skills with a computable label:", len(rows), " max |difference| from skills.csv:", round(max(check(r, sc) for r, sc in rows), 9))

for tgt in MODELS:
    g = []
    for r, sc in rows:
        gain = max(sc[w][1] - sc[w][0] for w in MODELS if w != tgt)
        if gain > 0.10:
            g.append(sc[tgt])
    n = len(g)
    unc = [(a < .4 and u < .4) for u, a in g]; intl = [(a >= .7 and u >= .7) for u, a in g]
    band = [abs(a - u) < .10 for u, a in g]; level = [u >= .8 for u, a in g]
    swept = sum(b and c for b, c in zip(band, unc)) / sum(unc)
    rec = lambda rule: sum(x and y for x, y in zip(rule, intl)) / sum(intl)
    print(f"{tgt:18s} gated {n}  uncovered {100*sum(unc)/n:.1f}%  band removes {100*swept:.1f}% of uncovered"
          f"  internalized removed: band {100*rec(band):.1f}% level {100*rec(level):.1f}%")
