"""curate.safety_gate — Stage-5 safety hard-gate over the LLM judgments.

Excludes from the released active set every skill whose LLM judgment fires a
hard-gate flag or scores safety < 3 (paper §3.3, conditions 2-3; condition 1,
the ``blocked.malware`` regex, already rejects at ingest in curate.safety).

Excluded skills are set ``active = 0`` so export (which requires ``active = 1``)
drops them. This runs AFTER ``license_audit activate`` in the build chain, so it
overrides the license-based activation for unsafe skills — the released
``active`` set is therefore the intersection of permissive-licensed and
safety-passing, matching the paper's active-set definition.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .quality import HARD_GATE_FLAGS


def run_safety_gate(db_path: str | Path) -> int:
    """Set ``active = 0`` for every currently-active skill whose judgment fires a
    hard-gate flag or has safety < 3. Returns the number of skills excluded."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT s.skill_id, q.subscores FROM skills s "
            "JOIN quality_judgments q ON q.content_hash = s.content_hash "
            "WHERE s.deleted = 0 AND s.active = 1"
        ).fetchall()
        excluded: list[str] = []
        for r in rows:
            try:
                sub = json.loads(r["subscores"] or "{}")
            except (ValueError, TypeError):
                continue
            if not isinstance(sub, dict):
                continue
            safety = sub.get("safety")
            flags = sub.get("flags")
            low_safety = isinstance(safety, (int, float)) and safety < 3
            hard_flag = isinstance(flags, list) and any(f in HARD_GATE_FLAGS for f in flags)
            if low_safety or hard_flag:
                excluded.append(r["skill_id"])
        if excluded:
            conn.executemany(
                "UPDATE skills SET active = 0 WHERE skill_id = ?",
                [(sid,) for sid in excluded],
            )
            conn.commit()
        return len(excluded)
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse

    from ..core.paths import INDEX_DB

    ap = argparse.ArgumentParser(description="Stage-5 safety hard-gate")
    ap.add_argument("--db", default=str(INDEX_DB), help="producer SQLite DB path")
    args = ap.parse_args()
    n = run_safety_gate(args.db)
    print(f"safety_gate: excluded {n} skills (active=0)")
