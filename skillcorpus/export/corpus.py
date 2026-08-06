"""export.corpus — write the open SkillCorpus dataset (parquet + attachments + card).

Contract: docs/corpus-schema.md. One row per skill, only ``deleted = 0 AND
active = 1`` (the GREEN-license gate). Reads the producer SQLite DB and writes::

    <out>/skills.parquet              one row per skill (21 columns)
    <out>/attachments.tar.zst         per-skill dirs (minus SKILL.md), <skill_id>/ prefix
    <out>/README.md                   dataset card

This is the final step of ``cli build``.
"""
from __future__ import annotations

import json
import sqlite3
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import zstandard

# Only rows that are not soft-deleted and pass the GREEN-license gate. The
# LEFT JOIN pulls the LLM judge's sub-scores when present (NULL otherwise).
_CORPUS_ROWS_SQL = """
    SELECT s.skill_id, s.name, s.description, s.body, s.frontmatter_raw,
           s.source, s.source_url, s.source_path, s.license, s.category,
           s.tags, s.quality_score, s.safety_flags, s.content_hash,
           s.body_tokens, s.has_scripts, s.has_references,
           s.added_at, s.updated_at, s.stored_path,
           q.subscores AS subscores_json
    FROM skills s
    LEFT JOIN quality_judgments q ON q.content_hash = s.content_hash
    WHERE s.deleted = 0 AND s.active = 1
    ORDER BY s.skill_id
"""

_SUBSCORES_TYPE = pa.struct(
    [("utility", pa.int8()), ("robustness", pa.int8()), ("safety", pa.int8())]
)

CORPUS_SCHEMA = pa.schema([
    ("skill_id", pa.string()),
    ("name", pa.string()),
    ("description", pa.string()),
    ("body", pa.string()),
    ("frontmatter_raw", pa.string()),
    ("source", pa.string()),
    ("source_url", pa.string()),
    ("source_path", pa.string()),
    ("license", pa.string()),
    ("category", pa.string()),
    ("tags", pa.list_(pa.string())),
    ("quality_score", pa.float64()),
    ("quality_subscores", _SUBSCORES_TYPE),
    ("safety_flags", pa.list_(pa.string())),
    ("content_hash", pa.string()),
    ("body_tokens", pa.int32()),
    ("has_scripts", pa.bool_()),
    ("has_references", pa.bool_()),
    ("added_at", pa.timestamp("us", tz="UTC")),
    ("updated_at", pa.timestamp("us", tz="UTC")),
    ("attachment_path", pa.string()),
])


def _json_list(raw: Any) -> list[str]:
    """Parse a JSON-array text column into list[str]; [] on empty / malformed."""
    if not raw:
        return []
    try:
        val = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return [str(x) for x in val] if isinstance(val, list) else []


def _subscores(raw: Any) -> dict[str, int | None] | None:
    """Extract the {utility, robustness, safety} 0-10 dims from the judge's
    subscores JSON. None when absent so the struct cell is null."""
    if not raw:
        return None
    try:
        val = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(val, dict):
        return None

    def _clip(x: Any) -> int | None:
        try:
            return max(0, min(10, int(x)))
        except (ValueError, TypeError):
            return None

    return {k: _clip(val.get(k)) for k in ("utility", "robustness", "safety")}


def _ts(raw: Any) -> datetime | None:
    """ISO-8601 string -> tz-aware UTC datetime; None on failure. Naive inputs
    are assumed UTC (the producer writes ``datetime.now(timezone.utc)``)."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _ensure_quality_judgments(conn: sqlite3.Connection) -> None:
    """The subscores LEFT JOIN needs the table to exist; a producer DB that
    never ran the LLM judge has none. Create it empty (idempotent, canonical
    schema) so the join yields NULL rather than ``no such table``."""
    from ..curate.quality import QUALITY_JUDGMENT_SCHEMA

    conn.executescript(QUALITY_JUDGMENT_SCHEMA)


def _add_attachments(
    tar: tarfile.TarFile, lib_root: Path | None, stored_path: str, skill_id: str
) -> str | None:
    """Add the skill directory (minus SKILL.md and dotfiles like .meta.json) to
    the tarball under ``<skill_id>/``. Returns that member prefix, or None when
    the skill bundles nothing beyond SKILL.md."""
    if not lib_root or not stored_path:
        return None
    src = Path(lib_root) / stored_path
    if not src.is_dir():
        return None
    prefix = skill_id.replace("/", "__")  # source can be owner/repo -> keep one flat member
    added = False
    for item in sorted(src.iterdir()):
        if item.name == "SKILL.md" or item.name.startswith("."):
            continue
        tar.add(item, arcname=f"{prefix}/{item.name}")
        added = True
    return prefix if added else None


def _write_dataset_card(out_dir: Path, table: pa.Table) -> None:
    def _tally(column: str) -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        for v in table.column(column).to_pylist():
            key = v or "UNKNOWN"
            counts[key] = counts.get(key, 0) + 1
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    lines = [
        "# SkillCorpus",
        "",
        f"Open dataset of {table.num_rows} skills, one row per skill. "
        "Column contract: `docs/corpus-schema.md`.",
        "",
        "## Sources",
        "",
        *[f"- `{name}`: {n}" for name, n in _tally("source")],
        "",
        "## Licenses",
        "",
        *[f"- `{name}`: {n}" for name, n in _tally("license")],
        "",
        "## Layout",
        "",
        "- `skills.parquet` — metadata + inline `body` (see the schema doc).",
        "- `attachments.tar.zst` — a zstd tarball; each skill's extra files "
        "(the dir minus `SKILL.md`) under an `<skill_id>/` member prefix.",
        "",
        "## Caveats",
        "",
        "- `quality_score` / `quality_subscores` are LLM-judged and noisy.",
        "- `safety_flags` are heuristic, not a security guarantee.",
        "- Near-duplicates across sources are merged (one winner kept).",
        "",
    ]
    (out_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def write_corpus(
    db_path: str | Path, lib_root: str | Path | None, out_dir: str | Path
) -> dict[str, Any]:
    """Write the corpus (parquet + attachments + card) from a producer DB.

    Returns a small stats dict: rows written, skills with attachments, out dir.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cols: dict[str, list[Any]] = {name: [] for name in CORPUS_SCHEMA.names}
    n_attach = 0

    raw = open(out_dir / "attachments.tar.zst", "wb")
    zf = zstandard.ZstdCompressor().stream_writer(raw)
    tar = tarfile.open(fileobj=zf, mode="w|")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        _ensure_quality_judgments(conn)
        for r in conn.execute(_CORPUS_ROWS_SQL):
            attach = _add_attachments(tar, lib_root, r["stored_path"], r["skill_id"])
            if attach:
                n_attach += 1
            cols["skill_id"].append(r["skill_id"])
            cols["name"].append(r["name"])
            cols["description"].append(r["description"])
            cols["body"].append(r["body"])
            cols["frontmatter_raw"].append(r["frontmatter_raw"])
            cols["source"].append(r["source"])
            cols["source_url"].append(r["source_url"])
            cols["source_path"].append(r["source_path"])
            cols["license"].append(r["license"])
            cols["category"].append(r["category"])
            cols["tags"].append(_json_list(r["tags"]))
            cols["quality_score"].append(float(r["quality_score"] or 0.0))
            cols["quality_subscores"].append(_subscores(r["subscores_json"]))
            cols["safety_flags"].append(_json_list(r["safety_flags"]))
            cols["content_hash"].append(r["content_hash"])
            cols["body_tokens"].append(int(r["body_tokens"] or 0))
            cols["has_scripts"].append(bool(r["has_scripts"]))
            cols["has_references"].append(bool(r["has_references"]))
            cols["added_at"].append(_ts(r["added_at"]))
            cols["updated_at"].append(_ts(r["updated_at"]))
            cols["attachment_path"].append(attach)
    finally:
        conn.close()
        tar.close()
        zf.close()
        raw.close()

    table = pa.table(
        {name: pa.array(cols[name], type=CORPUS_SCHEMA.field(name).type)
         for name in CORPUS_SCHEMA.names},
        schema=CORPUS_SCHEMA,
    )
    pq.write_table(table, out_dir / "skills.parquet")
    _write_dataset_card(out_dir, table)
    return {"rows": table.num_rows, "with_attachments": n_attach, "out": str(out_dir)}
