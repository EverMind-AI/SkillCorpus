# SPDX-License-Identifier: MIT
"""Recompute the rule audits of the paper from the dataset files (Python 3 standard library only).

Usage: python reproduce_audits.py path/to/dataset

The dataset is the paper's dataset (see ../README.md).

Prints, for the target model Qwen3.6-27B on the gated adversarial set:
  - Table 1 (adversarial columns): share retired, uncovered share, uncovered removed, internalized removed;
  - the state-by-rule counts of Appendix C;
then, on the skills scored on two tasks (all task-completion items of both tasks, as in Figure 3):
  - cross-task Cohen's kappa of the level rule, the band and the band restricted to A >= 0.7;
  - the cross-fitted audit (verdict on one task, state on the other, both directions);
and, from validation/skillsbench/task_condition_rewards.csv, the cost Delta of each rule (Table 4 point estimates).
"""
import csv, gzip, sys, statistics as st
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

if len(sys.argv) != 2:
    sys.exit("usage: python reproduce_audits.py path/to/dataset")
P = Path(sys.argv[1])
SC = P / "scores"; T = "Qwen3.6-27B"
skills = list(csv.DictReader(open(SC / "skills.csv", encoding="utf-8")))

# ---------- Table 1 and Appendix C (capability-item labels, gated set) ----------
g = [(float(r[f"U_{T}"]), float(r[f"A_{T}"])) for r in skills if r["score_computable"] == "1" and r[f"passes_gate_as_target_{T}"] == "1"]
state = lambda u, a: "internalized" if a >= .7 and u >= .7 else "skill works" if a >= .7 and u < .4 else "uncovered" if a < .4 and u < .4 else "skill hurts" if u >= .7 and a < .4 else "between thresholds"
rules = {"gain < 0": lambda u, a: a - u < 0, "gain <= 0": lambda u, a: a - u <= 0, "|gain| < 0.10": lambda u, a: abs(a - u) < .10,
         "band, A >= 0.7": lambda u, a: abs(a - u) < .10 and a >= .7, "U >= 0.8": lambda u, a: u >= .8}
n = len(g); S = [state(u, a) for u, a in g]
print(f"gated skills: {n}")
print(f"{'rule':16s} retired  uncov.share  uncov.removed  intl.removed")
for name, f in rules.items():
    ret = [f(u, a) for u, a in g]; R = sum(ret)
    unc = sum(r and s == "uncovered" for r, s in zip(ret, S)); intl = sum(r and s == "internalized" for r, s in zip(ret, S))
    print(f"{name:16s} {100*R/n:6.1f}%  {100*unc/max(R,1):9.1f}%  {100*unc/S.count('uncovered'):11.1f}%  {100*intl/S.count('internalized'):10.1f}%")
print("\nstate-by-rule counts (Appendix C):")
for st_ in ("internalized", "skill works", "uncovered", "skill hurts", "between thresholds"):
    idx = [i for i, s in enumerate(S) if s == st_]
    print(f"  {st_:18s} {len(idx):4d} " + " ".join(f"{sum(rules[k](*g[i]) for i in idx):4d}" for k in rules))

# ---------- cross-task analyses (all task-completion items) ----------
def tc_runs(path, task_col="task_id"):
    out = defaultdict(lambda: defaultdict(list))  # (task, arm) -> run -> [fraction]
    with gzip.open(path, "rt") as f:
        for r in csv.DictReader(f):
            if r["rubric"] != "task_completion" or r["model"] != T: continue
            mx = float(r["max_points"]); out[(r[task_col], r["arm"])][r["run"]].append(float(r["score"]) / mx if mx else 0.0)
    return out
first = tc_runs(SC / "item_runs.csv.gz"); second = tc_runs(SC / "second_task_item_runs.csv.gz")
means = defaultdict(list)
for r in csv.DictReader(open(SC / "target_unaided_item_means.csv", encoding="utf-8")): means[r["task_id"]].append(float(Fraction(r["mean_exact"])))
arm = lambda d, key: st.mean(st.mean(v) for v in d[key].values()) if d.get(key) else None
computable = {r["task_id"] for r in skills if r["score_computable"] == "1"}
pairs = []
seconds = sorted({t for t, _ in second})
for t2 in seconds:
    t1 = t2.rsplit("_", 1)[0] + "_0"
    if t1 not in computable or not means.get(t1): continue
    U1, A1 = st.mean(means[t1]), arm(first, (t1, "aided")); U2, A2 = arm(second, (t2, "unaided")), arm(second, (t2, "aided"))
    if None in (A1, U2, A2): continue
    pairs.append(((U1, A1), (U2, A2)))
def kappa(x, y):
    m = len(x); po = sum(a == b for a, b in zip(x, y)) / m; px, py = sum(x) / m, sum(y) / m
    pe = px * py + (1 - px) * (1 - py); return (po - pe) / (1 - pe)
xr = {"level": lambda u, a: u >= .8, "band": lambda u, a: abs(a - u) < .10, "band, A >= 0.7": lambda u, a: abs(a - u) < .10 and a >= .7}
print(f"\nskills scored on two tasks: {len(pairs)}")
for k, f in xr.items():
    v1 = [f(*p[0]) for p in pairs]; v2 = [f(*p[1]) for p in pairs]
    print(f"  cross-task kappa {k:15s} {kappa(v1, v2):.3f}   agreement {100*sum(a == b for a, b in zip(v1, v2))/len(pairs):.1f}%")
both = [(p[0], p[1]) for p in pairs] + [(p[1], p[0]) for p in pairs]  # (verdict task, state task)
unc = [(d, o) for d, o in both if state(*o) == "uncovered"]
print(f"  cross-fitted audit: of skills uncovered on the other task ({len(unc)} decisions), " +
      ", ".join(f"{k} retires {100*sum(f(*d) for d, _ in unc)/len(unc):.0f}%" for k, f in xr.items()))

# ---------- SkillsBench cost (Table 4 point estimates) ----------
rw = P / "validation" / "skillsbench" / "task_condition_rewards.csv"
if rw.exists():
    rows = list(csv.DictReader(open(rw, encoding="utf-8"))); full = {r["benchmark_task"]: float(r["mean_reward"]) for r in rows if r["condition"] == "complete_library"}
    print(f"\nSkillsBench, {len(full)} benchmark tasks, cost Delta in points of mean reward:")
    for c in ("level", "binary_band", "band_all_items", "band_matched_volume"):
        d = {r["benchmark_task"]: full[r["benchmark_task"]] - float(r["mean_reward"]) for r in rows if r["condition"] == c and r["mean_reward"]}
        print(f"  {c:20s} {100*sum(d.values())/len(full):+.1f}  (touches {len(d)} tasks)")
