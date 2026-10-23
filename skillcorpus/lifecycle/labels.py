# SPDX-License-Identifier: MIT
"""Unaided and aided capability scores, the validity gate and the three states.

For one task and one model, a run's capability score is the mean of ``score / max`` over the task's capability items
of the task-completion rubric. U is the mean of that score over the unaided runs and A the mean over the aided runs.
A task passes the validity gate for a target model when some other (witness) model gains more than ``gamma`` from
the skill on the same items, ``max_w (A_w - U_w) > gamma``. The states use a high threshold ``h`` and a low one
``l``: internalized (A, U >= h), skill works (A >= h, U < l) and uncovered (A, U < l).

CLI: build a labels CSV from judged runs (``run_arms.py`` output) and the capability-item file
(``capability_items.py`` output). Each ``--runs MODEL ARM DIR`` adds one run directory; give one per run.

  python -m skillcorpus.lifecycle.labels --capability capability_items.jsonl \\
      --runs Qwen3.6-27B unaided runs/q36/unaided/r1 --runs Qwen3.6-27B aided runs/q36/aided/r1 ... -o labels.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics as st
from collections import defaultdict
from pathlib import Path

H, L, GAMMA = 0.7, 0.4, 0.1


def load_capability_items(path: Path) -> dict[str, set[str]]:
    """{task_id: names of its capability items} from capability_items.py output (items with needs_doc true)."""
    out: dict[str, set[str]] = {}
    for line in open(path, encoding="utf-8"):
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("capability"):
            out[rec["task_id"]] = {k for k, v in rec["capability"].items() if v}
    return out


def item_fractions(result: dict, rubric: str = "task_completion") -> dict[str, float]:
    """{item: score / max} of one judged run (result.json written by judge.judge_workspace)."""
    detail = (result.get(rubric) or {}).get("detail") or []
    return {d["name"]: (float(d["score"]) / float(d["max"]) if float(d["max"]) else 0.0) for d in detail}


def run_score(fractions: dict[str, float], capability: set[str]) -> float | None:
    """Capability score of one run: mean over the capability items that were scored."""
    vals = [fractions[i] for i in capability if i in fractions]
    return st.mean(vals) if vals else None


def arm_score(runs: list[dict[str, float]], capability: set[str]) -> float | None:
    """U or A: mean over runs of the run-level capability score."""
    per = [s for s in (run_score(r, capability) for r in runs) if s is not None]
    return st.mean(per) if per else None


def passes_gate(witness_labels: list[tuple[float, float]], gamma: float = GAMMA) -> bool:
    """Validity gate from the (U, A) pairs of the witness models (the target model excluded)."""
    return any(a - u > gamma for u, a in witness_labels)


def state(u: float, a: float, h: float = H, l: float = L) -> str:
    """State of a skill for one model; ``skill hurts`` and ``between thresholds`` cover the remaining cases."""
    if a >= h and u >= h:
        return "internalized"
    if a >= h and u < l:
        return "skill works"
    if a < l and u < l:
        return "uncovered"
    if u >= h and a < l:
        return "skill hurts"
    return "between thresholds"


def collect_runs(run_dir: Path) -> dict[str, dict[str, float]]:
    """{task_id: item fractions} of every judged task in one run directory (runs the judge failed to score are left
    out)."""
    out = {}
    for case in sorted(Path(run_dir).iterdir()):
        rp = case / "result.json"
        if rp.is_file():
            fractions = item_fractions(json.loads(rp.read_text(encoding="utf-8")))
            if fractions:
                out[case.name] = fractions
    return out


def build_labels(capability: dict[str, set[str]], runs: list[tuple[str, str, Path]]) -> list[dict]:
    """One row per (task, model) with U, A and the number of runs per arm."""
    by = defaultdict(list)  # (task, model, arm) -> [fractions per run]
    for model, arm, d in runs:
        for task, fr in collect_runs(d).items():
            by[(task, model, arm)].append(fr)
    rows = []
    for task, model in sorted({(t, m) for t, m, _ in by}):
        cap = capability.get(task)
        if not cap:
            continue
        u_runs, a_runs = by.get((task, model, "unaided"), []), by.get((task, model, "aided"), [])
        rows.append({"task_id": task, "model": model, "U": arm_score(u_runs, cap), "A": arm_score(a_runs, cap),
                     "n_unaided": len(u_runs), "n_aided": len(a_runs), "n_capability_items": len(cap)})
    return rows


def read_labels(path: Path) -> dict[str, dict[str, tuple[float | None, float | None]]]:
    """{task_id: {model: (U, A)}} from a labels CSV."""
    def num(x):
        return float(x) if x not in ("", "None", None) else None

    out: dict = defaultdict(dict)
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["task_id"]][r["model"]] = (num(r["U"]), num(r["A"]))
    return dict(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Compute U and A per task and model from judged runs.")
    ap.add_argument("--capability", required=True, help="capability_items.py output (JSONL)")
    ap.add_argument("--runs", nargs=3, action="append", metavar=("MODEL", "ARM", "DIR"), required=True,
                    help="one judged run directory; repeat for every run of every model and arm")
    ap.add_argument("-o", "--out", required=True, help="labels CSV")
    a = ap.parse_args(argv)
    bad = [arm for _, arm, _ in a.runs if arm not in ("unaided", "aided")]
    if bad:
        ap.error(f"ARM must be unaided or aided, got {bad[0]!r}")
    rows = build_labels(load_capability_items(Path(a.capability)), [(m, arm, Path(d)) for m, arm, d in a.runs])
    cols = ["task_id", "model", "U", "A", "n_unaided", "n_aided", "n_capability_items"]
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"[labels] {len(rows)} (task, model) rows -> {a.out}")


if __name__ == "__main__":
    main()
