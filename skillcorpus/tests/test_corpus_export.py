"""Corpus export tests — write_corpus() over a synthetic producer DB.

Covers the docs/corpus-schema.md contract: the deleted/active row filter, the
native list / struct / timestamp column types, and the attachments rule (the
skill dir minus SKILL.md and dotfiles).
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pyarrow.parquet as pq

from skillcorpus.core.models import SCHEMA_SQL
from skillcorpus.curate.quality import QUALITY_JUDGMENT_SCHEMA
from skillcorpus.export.corpus import CORPUS_SCHEMA, write_corpus

_TS = "2026-08-05T12:00:00+00:00"


def _insert(conn: sqlite3.Connection, **over: object) -> None:
    row = {
        "skill_id": "anthropics__demo__abcd1234",
        "name": "demo",
        "description": "a demo skill",
        "body": "# demo\n\nbody text",
        "frontmatter_raw": "{}",
        "source": "anthropics",
        "source_url": "https://example.com/repo",
        "source_path": "skills/demo",
        "license": "MIT",
        "content_hash": "h1",
        "name_hash": "nh1",
        "category": "DEV",
        "tags": '["pdf", "reportlab"]',
        "quality_score": 0.8,
        "safety_flags": "[]",
        "body_tokens": 42,
        "has_scripts": 1,
        "has_references": 0,
        "deleted": 0,
        "superseded_by": None,
        "added_at": _TS,
        "updated_at": _TS,
        "stored_path": "skills/anthropics/demo",
        "active": 1,
    }
    row.update(over)
    cols = ", ".join(row)
    conn.execute(
        f"INSERT INTO skills ({cols}) VALUES ({', '.join('?' for _ in row)})",
        list(row.values()),
    )


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_SQL)
    conn.executescript(QUALITY_JUDGMENT_SCHEMA)
    # exported row + a quality judgment (for the subscores struct)
    _insert(conn)
    conn.execute(
        "INSERT INTO quality_judgments (content_hash, score, reason, judged_at, subscores) "
        "VALUES (?, ?, ?, ?, ?)",
        ("h1", 8.0, "ok", _TS, '{"utility": 9, "robustness": 8, "safety": 7, "flags": []}'),
    )
    # excluded: soft-deleted, and not-yet-license-gated (active=0)
    _insert(conn, skill_id="s__deleted__x", content_hash="h2", name_hash="nh2",
            stored_path="skills/x/deleted", deleted=1)
    _insert(conn, skill_id="s__inactive__x", content_hash="h3", name_hash="nh3",
            stored_path="skills/x/inactive", active=0)
    conn.commit()
    conn.close()


def _make_skill_dir(lib_root: Path) -> None:
    d = lib_root / "skills" / "anthropics" / "demo"
    (d / "scripts").mkdir(parents=True)
    (d / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    (d / "scripts" / "run.py").write_text("print('hi')\n", encoding="utf-8")
    (d / ".meta.json").write_text('{"source": "anthropics"}', encoding="utf-8")


def test_write_corpus_full():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        db = tmp_p / "mass_library.db"
        lib = tmp_p / "lib"
        out = tmp_p / "corpus"
        _make_db(db)
        _make_skill_dir(lib)

        stats = write_corpus(db, lib, out)

        # only the active, non-deleted row is exported
        assert stats["rows"] == 1, stats
        assert stats["with_attachments"] == 1, stats

        table = pq.read_table(out / "skills.parquet")
        assert table.schema.equals(CORPUS_SCHEMA), table.schema
        rec = table.to_pylist()[0]

        assert rec["skill_id"] == "anthropics__demo__abcd1234"
        assert rec["tags"] == ["pdf", "reportlab"]          # native list<string>
        assert rec["safety_flags"] == []
        assert rec["quality_subscores"] == {"utility": 9, "robustness": 8, "safety": 7}
        assert abs(rec["quality_score"] - 0.8) < 1e-9
        assert rec["added_at"].isoformat().startswith("2026-08-05T12:00:00")
        assert rec["attachment_path"] == "attachments/anthropics__demo__abcd1234"

        # attachments: scripts copied, SKILL.md and dotfiles skipped
        adir = out / "attachments" / "anthropics__demo__abcd1234"
        assert (adir / "scripts" / "run.py").exists()
        assert not (adir / "SKILL.md").exists()
        assert not (adir / ".meta.json").exists()

        assert (out / "README.md").read_text(encoding="utf-8").startswith("# SkillCorpus")


def test_write_corpus_empty_db_no_judgments():
    """An empty DB (no quality_judgments table) must not raise on the LEFT JOIN."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        db = tmp_p / "empty.db"
        conn = sqlite3.connect(db)
        conn.executescript(SCHEMA_SQL)  # skills only, no quality_judgments
        conn.commit()
        conn.close()

        stats = write_corpus(db, None, tmp_p / "out")
        assert stats["rows"] == 0
        table = pq.read_table(tmp_p / "out" / "skills.parquet")
        assert table.schema.equals(CORPUS_SCHEMA)
        assert table.num_rows == 0


if __name__ == "__main__":
    test_write_corpus_full()
    test_write_corpus_empty_db_no_judgments()
    print("CORPUS EXPORT TESTS PASSED")
