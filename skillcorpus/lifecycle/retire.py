# SPDX-License-Identifier: MIT
"""Level-rule retirement of a skill for one deployment model (Algorithm 1 of the paper).

The verdict is a certificate, issued for one model, that the model covers the skill's capability without the
skill. ``decide`` turns the measurements of one skill into the next step:

* ``extra_tasks``   U lies within ``m`` of ``tau`` on fewer than three tasks: add two tasks from the same generation
                    batch, run each once unaided, and decide again on the mean over the three tasks.
* ``retire``        U >= tau and some witness model gains more than ``gamma`` from the skill: soft-retire the skill
                    for this model (hide it from this model's retrieval, keep it in the hub and restorable).
* ``regenerate``    U >= tau but no witness gains, so the task may not test the skill: regenerate the task once.
* ``keep_no_verdict`` the regenerated task fails the gate too: keep the skill without a verdict.
* ``measure_aided`` U < l and the model's aided score is not known yet: run the aided arm.
* ``keep_repair``   U < l and A < l: the capability is uncovered, keep the skill and flag it for repair.
* ``keep``          every other case.

The gate is consulted only before retiring, since a low score already rules out the easy-task explanation, and the
aided arm of the deployment model is needed only to flag uncovered skills. Witness evidence is reused across models.

CLI: decide every skill in a labels CSV (``labels.py`` output) for one target model; the other models in the file act
as witnesses.

  python -m skillcorpus.lifecycle.retire --labels labels.csv --target Qwen3.6-27B -o decisions.csv
"""
from __future__ import annotations

import argparse
import csv
import statistics as st
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from ._layout import skill_of
from .labels import GAMMA, L, read_labels

TAU, MARGIN, MAX_TASKS = 0.8, 0.2, 3
_EPS = 1e-9  # so that a U of exactly tau - m or tau + m counts as within the margin despite float rounding


@dataclass(frozen=True)
class Decision:
    verdict: str
    reason: str


def decide(u: float, witness_gains: list[float], a: float | None = None, *, n_tasks: int = 1,
           regenerated: bool = False, tau: float = TAU, gamma: float = GAMMA, low: float = L,
           margin: float = MARGIN, max_tasks: int = MAX_TASKS) -> Decision:
    """Next step for one skill and one deployment model.

    u: the model's unaided capability score, averaged over the skill's tasks so far.
    witness_gains: A_w - U_w of each witness model on the skill's tasks.
    a: the model's aided score (only needed when u < low).
    n_tasks: number of tasks u is averaged over; regenerated: whether the task was already regenerated once.
    In the paper the witnesses are scored on the skill's first task only (the extra tasks are run unaided by the
    deployment model alone), so ``witness_gains`` is the gate of that task.
    """
    if abs(u - tau) <= margin + _EPS and n_tasks < max_tasks:
        return Decision("extra_tasks", f"U={u:.3f} is within {margin} of tau on {n_tasks} task(s)")
    if u >= tau:
        best = max(witness_gains) if witness_gains else None
        if best is not None and best > gamma:
            return Decision("retire", f"U={u:.3f} >= {tau} and a witness gains {best:.3f} > {gamma}")
        if not regenerated:
            return Decision("regenerate", f"U={u:.3f} >= {tau} but no witness gains more than {gamma}")
        return Decision("keep_no_verdict", "the regenerated task still fails the validity gate")
    if u < low:
        if a is None:
            return Decision("measure_aided", f"U={u:.3f} < {low}; the aided score is needed to check for repair")
        if a < low:
            return Decision("keep_repair", f"U={u:.3f} and A={a:.3f} are both below {low}: uncovered")
    return Decision("keep", f"U={u:.3f} < {tau}")


def decide_from_labels(labels: dict, target: str, **kw) -> list[dict]:
    """Group the tasks of each skill and decide every skill for ``target``; the other models are witnesses."""
    by_skill = defaultdict(list)
    for task, per_model in labels.items():
        if target in per_model and per_model[target][0] is not None:
            by_skill[skill_of(task)].append(task)
    rows = []
    for skill, tasks in sorted(by_skill.items()):
        us = [labels[t][target][0] for t in tasks]
        aas = [labels[t][target][1] for t in tasks if labels[t][target][1] is not None]
        gains = [a_w - u_w for t in tasks for m, (u_w, a_w) in labels[t].items()
                 if m != target and u_w is not None and a_w is not None]
        u = st.mean(us)
        a = st.mean(aas) if aas else None
        d = decide(u, gains, a, n_tasks=len(tasks), **kw)
        rows.append({"skill": skill, "target": target, "tasks": ";".join(sorted(tasks)), "U": round(u, 6),
                     "A": None if a is None else round(a, 6),
                     "max_witness_gain": round(max(gains), 6) if gains else None,
                     "verdict": d.verdict, "reason": d.reason})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="Level-rule retirement decisions for one deployment model.")
    ap.add_argument("--labels", required=True, help="labels CSV from labels.py")
    ap.add_argument("--target", required=True, help="deployment model the verdicts are issued for")
    ap.add_argument("--tau", type=float, default=TAU)
    ap.add_argument("--gamma", type=float, default=GAMMA)
    ap.add_argument("--low", type=float, default=L)
    ap.add_argument("--margin", type=float, default=MARGIN)
    ap.add_argument("--max-tasks", type=int, default=MAX_TASKS, help="tasks per skill after the extra tasks")
    ap.add_argument("--regenerated", action="store_true",
                    help="every task in the file is already a regenerated one (a failed gate then gives "
                         "keep_no_verdict); decide regenerated skills in a separate labels file")
    ap.add_argument("-o", "--out", required=True, help="decisions CSV")
    a = ap.parse_args(argv)
    rows = decide_from_labels(read_labels(Path(a.labels)), a.target, tau=a.tau, gamma=a.gamma, low=a.low,
                              margin=a.margin, max_tasks=a.max_tasks, regenerated=a.regenerated)
    cols = ["skill", "target", "tasks", "U", "A", "max_witness_gain", "verdict", "reason"]
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    counts = defaultdict(int)
    for r in rows:
        counts[r["verdict"]] += 1
    print(f"[retire] {len(rows)} skills for {a.target}: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    main()
