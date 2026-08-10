"""curate.safety_gate — Stage-5 safety hard-gate over the LLM judgments.

Excludes from the released set every skill whose LLM judgment fires a hard-gate
flag or scores safety < 3 (paper §3.3, conditions 2-3; condition 1, the
``blocked.malware`` regex, already rejects at ingest in curate.safety).

Excluded skills are soft-deleted (``deleted = 1``), NOT set ``active = 0``:
``active`` is the license bit that ``license_audit activate`` owns and would
re-set to 1, so using it here lets a later ``activate`` silently revive an
excluded skill. ``deleted`` is orthogonal (``activate`` only touches rows with
``deleted = 0``) and is already an export filter, so the exclusion is permanent
and order-independent. ``superseded_by`` stays NULL, distinguishing a
safety exclusion from a near-duplicate merge loser.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .quality import HARD_GATE_FLAGS, ensure_quality_judgments


def _is_number(x) -> bool:
    # bool is a subclass of int; a JSON ``true`` must not count as the number 1.
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def run_safety_gate(db_path: str | Path) -> int:
    """Soft-delete (``deleted = 1``) every non-deleted skill whose judgment fires
    a hard-gate flag or has safety < 3. Returns the number of skills excluded."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")  # wait out lock contention (lib conn may be open)
    try:
        ensure_quality_judgments(conn)  # a no-LLM build has no judgments table yet
        rows = conn.execute(
            "SELECT s.skill_id, q.subscores FROM skills s "
            "JOIN quality_judgments q ON q.content_hash = s.content_hash "
            "WHERE s.deleted = 0"
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
            low_safety = _is_number(safety) and safety < 3
            hard_flag = isinstance(flags, list) and any(f in HARD_GATE_FLAGS for f in flags)
            if low_safety or hard_flag:
                excluded.append(r["skill_id"])
        if excluded:
            conn.executemany(
                "UPDATE skills SET deleted = 1 WHERE skill_id = ?",
                [(sid,) for sid in excluded],
            )
            conn.commit()
        # The gate can only judge skills that have an LLM judgment. Any active
        # skill without one (e.g. a build with no reachable LLM) has NOT passed
        # conditions 2-3 — warn loudly rather than let it pass silently, so the
        # "active = safety-vetted" invariant is not quietly violated.
        unjudged = conn.execute(
            "SELECT COUNT(*) FROM skills s WHERE s.deleted = 0 AND s.active = 1 "
            "AND NOT EXISTS (SELECT 1 FROM quality_judgments q "
            "                WHERE q.content_hash = s.content_hash)"
        ).fetchone()[0]
        if unjudged:
            print(
                f"  WARNING: {unjudged} active skills have no LLM judgment — the "
                f"safety hard-gate (safety<3 / hard-gate flags) was NOT applied to "
                f"them, so the active set is NOT fully safety-vetted. Run "
                f"quality_pass with a reachable LLM to close the gap.",
                flush=True,
            )
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
    print(f"safety_gate: excluded {n} skills (deleted=1)")
