# SPDX-License-Identifier: MIT
"""Audit retirement rules against the states they remove (Table 1 and Appendix C of the paper).

Gain-based rules read only the gain A - U. The audit applies each rule to the skills that pass the validity gate for
a target model (one (U, A) pair per skill) and reports, per rule, the share of the library it retires, the share of its retirements that is
uncovered, and how much of the uncovered and of the internalized state it removes. The states come from
``labels.state``; they share a measurement with the verdicts, so the audit shows which skills each rule selects.

CLI:
  python -m skillcorpus.lifecycle.audit --labels labels.csv --target Qwen3.6-27B
"""
from __future__ import annotations

import argparse
import statistics as st
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from ._layout import skill_of
from .labels import GAMMA, passes_gate, read_labels, state
from .retire import TAU

Rule = Callable[[float, float], bool]  # (u, a) -> retire?

RULES: dict[str, Rule] = {
    "gain < 0": lambda u, a: a - u < 0,
    "gain <= 0": lambda u, a: a - u <= 0,
    "|gain| < 0.10": lambda u, a: abs(a - u) < 0.10,
    "|gain| < 0.10, A >= 0.7": lambda u, a: abs(a - u) < 0.10 and a >= 0.7,
    f"U >= {TAU}": lambda u, a: u >= TAU,
}
STATES = ("internalized", "skill works", "uncovered", "skill hurts", "between thresholds")


def gated_pairs(labels: dict, target: str, gamma: float = GAMMA) -> list[tuple[float, float]]:
    """(U, A) of the target model for every skill that passes the validity gate for it, one pair per skill.

    As in ``retire.decide_from_labels``, a skill with several tasks gets the mean of the target's U and A over its
    tasks, and it passes the gate when some witness gains more than ``gamma`` on one of them."""
    by_skill = defaultdict(list)
    for task, per_model in labels.items():
        by_skill[skill_of(task)].append(per_model)
    out = []
    for per_task in by_skill.values():
        ua = [pm[target] for pm in per_task if target in pm and None not in pm[target]]
        if not ua:
            continue
        witnesses = [v for pm in per_task for m, v in pm.items() if m != target and None not in v]
        if passes_gate(witnesses, gamma):
            out.append((st.mean(u for u, _ in ua), st.mean(a for _, a in ua)))
    return out


def audit(pairs: list[tuple[float, float]], rules: dict[str, Rule] = RULES) -> dict:
    """Per-rule shares and the state-by-rule counts over (U, A) pairs."""
    states = [state(u, a) for u, a in pairs]
    n_unc, n_int = states.count("uncovered"), states.count("internalized")
    table = {}
    for name, rule in rules.items():
        ret = [rule(u, a) for u, a in pairs]
        r = sum(ret)
        unc = sum(x and s == "uncovered" for x, s in zip(ret, states))
        intl = sum(x and s == "internalized" for x, s in zip(ret, states))
        table[name] = {"retired": r / len(pairs) if pairs else 0.0,
                       "uncovered_share": unc / r if r else 0.0,
                       "uncovered_removed": unc / n_unc if n_unc else 0.0,
                       "internalized_removed": intl / n_int if n_int else 0.0}
    counts = {s: {name: sum(rule(u, a) for (u, a), st_ in zip(pairs, states) if st_ == s)
                  for name, rule in rules.items()} for s in STATES}
    sizes = {s: states.count(s) for s in STATES}
    return {"n": len(pairs), "table": table, "state_counts": counts, "state_sizes": sizes}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Audit retirement rules on the gated skills of one target model.")
    ap.add_argument("--labels", required=True, help="labels CSV from labels.py")
    ap.add_argument("--target", required=True)
    ap.add_argument("--gamma", type=float, default=GAMMA)
    a = ap.parse_args(argv)
    res = audit(gated_pairs(read_labels(Path(a.labels)), a.target, a.gamma))
    print(f"gated skills for {a.target}: {res['n']}")
    print(f"{'rule':26s} retired  uncov.share  uncov.removed  intl.removed")
    for name, row in res["table"].items():
        print(f"{name:26s} {100 * row['retired']:6.1f}%  {100 * row['uncovered_share']:9.1f}%  "
              f"{100 * row['uncovered_removed']:11.1f}%  {100 * row['internalized_removed']:10.1f}%")
    print("\nstate-by-rule counts:")
    for s in STATES:
        print(f"  {s:18s} {res['state_sizes'][s]:5d} " + " ".join(f"{res['state_counts'][s][k]:5d}" for k in RULES))


if __name__ == "__main__":
    main()
