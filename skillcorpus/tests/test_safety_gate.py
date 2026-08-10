"""Safety-gate tests — Stage-5 exclusion (safety < 3 / hard-gate flags) over a
synthetic producer DB. Soft flags and clean skills must survive; excluded skills
are set active=0 (dropped by export)."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from skillcorpus.core.models import SCHEMA_SQL
from skillcorpus.curate.quality import QUALITY_JUDGMENT_SCHEMA
from skillcorpus.curate.safety_gate import run_safety_gate

_TS = "2026-08-06T12:00:00+00:00"


def _skill(conn: sqlite3.Connection, skill_id: str, content_hash: str, **over: object) -> None:
    row = {
        "skill_id": skill_id, "name": skill_id, "description": "a demo skill",
        "body": "# demo\n\nbody", "frontmatter_raw": "{}", "source": "anthropics",
        "source_url": "https://example.com/repo", "source_path": f"skills/{skill_id}",
        "license": "MIT",
        "content_hash": content_hash, "name_hash": f"nh_{skill_id}",
        "category": "DEV", "tags": "[]", "quality_score": 0.8, "safety_flags": "[]",
        "body_tokens": 10, "has_scripts": 0, "has_references": 0, "deleted": 0,
        "superseded_by": None, "added_at": _TS, "updated_at": _TS,
        "stored_path": f"skills/{skill_id}", "active": 1,
    }
    row.update(over)
    cols = ", ".join(row)
    conn.execute(
        f"INSERT INTO skills ({cols}) VALUES ({', '.join('?' for _ in row)})",
        list(row.values()),
    )


def _judge(conn: sqlite3.Connection, content_hash: str, subscores_json: str) -> None:
    conn.execute(
        "INSERT INTO quality_judgments (content_hash, score, reason, judged_at, subscores) "
        "VALUES (?, ?, ?, ?, ?)",
        (content_hash, 8.0, "ok", _TS, subscores_json),
    )


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_SQL)
    conn.executescript(QUALITY_JUDGMENT_SCHEMA)
    _skill(conn, "clean", "h_clean")
    _judge(conn, "h_clean", '{"utility": 9, "robustness": 8, "safety": 8, "flags": []}')
    _skill(conn, "lowsafety", "h_low")
    _judge(conn, "h_low", '{"utility": 9, "robustness": 8, "safety": 1, "flags": []}')
    _skill(conn, "hardflag", "h_hard")
    _judge(conn, "h_hard", '{"utility": 6, "robustness": 4, "safety": 8, "flags": ["cmd_injection"]}')
    _skill(conn, "softflag", "h_soft")
    _judge(conn, "h_soft", '{"utility": 8, "robustness": 2, "safety": 8, "flags": ["placeholder"]}')
    conn.commit()
    conn.close()


def _deleted(path: Path, skill_id: str) -> int:
    conn = sqlite3.connect(path)
    row = conn.execute("SELECT deleted FROM skills WHERE skill_id=?", (skill_id,)).fetchone()
    conn.close()
    return row[0]


def test_safety_gate_excludes_unsafe():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "index.db"
        _make_db(db)
        assert run_safety_gate(db) == 2         # lowsafety + hardflag
        assert _deleted(db, "clean") == 0       # safe → stays
        assert _deleted(db, "lowsafety") == 1   # safety<3 → excluded (deleted)
        assert _deleted(db, "hardflag") == 1    # hard-gate flag → excluded (deleted)
        assert _deleted(db, "softflag") == 0    # soft flag only → stays


def test_safety_gate_no_judgment_and_idempotent(capsys):
    """A skill without a judgment is left active (fail-open) but WARNED about; a
    second run excludes nothing new."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "index.db"
        _make_db(db)
        conn = sqlite3.connect(db)
        _skill(conn, "nojudge", "h_none")       # no matching judgment row
        conn.commit()
        conn.close()
        assert run_safety_gate(db) == 2
        assert _deleted(db, "nojudge") == 0
        out = capsys.readouterr().out
        assert "no LLM judgment" in out and "not fully safety-vetted" in out.lower(), out
        assert run_safety_gate(db) == 0         # already excluded, nothing new


def test_safety_gate_no_judgments_table():
    """A DB that never ran the LLM judge (no quality_judgments table at all) must
    NOT crash with 'no such table' — ensure_quality_judgments creates it first."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "index.db"
        conn = sqlite3.connect(db)
        conn.executescript(SCHEMA_SQL)   # skills only, NO quality_judgments table
        _skill(conn, "s1", "h1")
        conn.commit()
        conn.close()
        assert run_safety_gate(db) == 0          # excludes nothing, does not raise
        assert _deleted(db, "s1") == 0


if __name__ == "__main__":
    test_safety_gate_excludes_unsafe()   # (capsys-based tests need pytest)
    test_safety_gate_no_judgments_table()
    print("SAFETY GATE TESTS PASSED")
