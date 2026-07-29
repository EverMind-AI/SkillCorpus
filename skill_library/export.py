"""Export producer ``skill_library/data/index.db`` → Ever-v2 ``mass_library.db``.

Replaces the older ``sync_to_everclaw.py`` filesystem-mirror path with a
single-file SQLite export targeting Ever-v2's SqliteStore schema. The
consumer then attaches this DB via ``config.skill_forge.mass_library_db``
and reads metadata + body + embeddings without touching the producer
filesystem.

Schema mapping (producer SkillRecord → Ever-v2 skills row):
    skill_id           ─→ (dropped, Ever-v2 uses INTEGER AUTOINC id)
    name               ─→ name
    description        ─→ description
    body               ─→ body
    source             ─→ source
    stored_path        ─→ path (NULL — consumer doesn't have producer fs)
    frontmatter_raw    ─→ frontmatter_json
    license            ─→ license
    category           ─→ category
    content_hash       ─→ content_hash
    quality_score      ─→ (folded into frontmatter_json.everclaw.quality_score)
    tags / safety_flags─→ (folded into frontmatter_json.everclaw.*)
    deleted=0          (filter — only non-deleted rows are exported)
    active=1           (filter — only commercially-deployable licenses)
    embedding (vec_skills FLOAT[1024]) ─→ embedding BLOB (float32 .tobytes())

Usage:
    python -m skill_library.export \\
        [--src skill_library/data] \\
        [--dst /path/to/mass_library.db] \\
        [--limit N] \\
        [--refresh-endpoint http://producer-host:8765]

After producing the DB, set on the consumer side::

    "skill_forge": { "mass_library_db": "/path/to/mass_library.db" }

The ``.refresh_endpoint`` sentinel (when ``--refresh-endpoint`` is given)
is written next to ``mass_library.db`` so consumers using
``everclaw skill refresh`` auto-discover the producer URL.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import shutil
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = REPO_ROOT / "skill_library" / "data"
DEFAULT_DST = REPO_ROOT / "mass_library.db"
_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


def _config_embedding() -> tuple[str, int]:
    """Read embedding.model / embedding.dim from config.yaml as the export defaults.

    Ensures the exported embedding label matches this repo's config (= the consumer-side config),
    avoiding a hard-coded stale label that would silently degrade the mass pool to BM25-only.
    Falls back to conservative defaults when config is missing.
    """
    try:
        import yaml
        cfg = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        emb = cfg.get("embedding", {}) or {}
        return str(emb.get("model", "embedding-our")), int(emb.get("dim", 1024))
    except Exception:
        return "embedding-our", 1024


_EVER_V2_SCHEMA = """
CREATE TABLE IF NOT EXISTS skills (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    name                 TEXT NOT NULL,
    source               TEXT NOT NULL,
    description          TEXT NOT NULL DEFAULT '',
    body                 TEXT NOT NULL,
    path                 TEXT,
    is_always            INTEGER NOT NULL DEFAULT 0,
    requires_json        TEXT NOT NULL DEFAULT '{}',
    frontmatter_json     TEXT NOT NULL DEFAULT '{}',
    scope                TEXT,
    license              TEXT,
    category             TEXT,
    content_hash         TEXT NOT NULL,
    embedding            BLOB,
    embedding_model      TEXT,
    embedding_dim        INTEGER,
    embedding_dtype      TEXT,
    imported_at          INTEGER,
    created_at           INTEGER NOT NULL,
    updated_at           INTEGER NOT NULL,
    confidence           REAL    NOT NULL DEFAULT 0.5,
    source_case_ids_json TEXT    NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_skills_source       ON skills(source);
CREATE INDEX IF NOT EXISTS idx_skills_category     ON skills(category);
CREATE INDEX IF NOT EXISTS idx_skills_is_always    ON skills(is_always) WHERE is_always = 1;
CREATE INDEX IF NOT EXISTS idx_skills_name_source  ON skills(name, source);
"""


def _load_embeddings_from_vec_skills(data_dir: Path) -> dict[str, bytes]:
    """Load embeddings from the producer's ``vec_skills`` sqlite-vec table.

    ``vec_skills`` is the canonical source of truth (every active skill
    has a row; ingest writes here first). Earlier versions of this script
    read from ``skill_index.faiss``, but the faiss index can fall behind
    ``vec_skills`` if it isn't rebuilt after bulk ingest — and the export
    silently dropped 20K rows because of exactly that staleness. Reading
    sqlite-vec directly removes the failure mode; faiss is kept only as
    a runtime accelerator for ``storage.py:vector_search``.
    """
    db_path = data_dir / "index.db"
    if not db_path.exists():
        print(f"!! producer DB not found at {db_path}", file=sys.stderr)
        return {}
    try:
        import sqlite_vec
    except ImportError:
        print("!! sqlite-vec not installed (pip install sqlite-vec); "
              "embeddings will be empty in output", file=sys.stderr)
        return {}

    conn = sqlite3.connect(str(db_path))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)

    # vec_skills stores embeddings as raw float32 bytes already, in the
    # exact format the consumer expects (no re-packing needed).
    out: dict[str, bytes] = {}
    n_total = conn.execute("SELECT COUNT(*) FROM vec_skills").fetchone()[0]
    print(f"loading {n_total} embeddings from vec_skills (sqlite-vec)...",
          flush=True)
    t0 = time.time()
    for i, (skill_id, emb_blob) in enumerate(
        conn.execute("SELECT skill_id, embedding FROM vec_skills")
    ):
        out[skill_id] = bytes(emb_blob)
        if (i + 1) % 10000 == 0:
            elapsed = time.time() - t0
            print(f"  {i+1}/{n_total} ({(i+1)/elapsed:.0f}/s)", flush=True)
    conn.close()
    print(f"  loaded {len(out)} embeddings in {time.time()-t0:.0f}s",
          flush=True)
    return out


def _parse_added_at(s: str | None) -> int | None:
    """Producer ``added_at`` is ISO-8601 (``2026-04-22T03:35:04.356029+00:00``);
    consumer expects Unix seconds INTEGER. None / parse-failure → ``None``."""
    if not s:
        return None
    try:
        # ``fromisoformat`` handles both ``+00:00`` offsets and naive forms.
        from datetime import datetime as _dt
        return int(_dt.fromisoformat(s).timestamp())
    except (ValueError, TypeError):
        return None


# Body actually expects on-disk assets when it names a file (with
# extension) inside ``scripts/`` or ``references/``. The file-extension
# anchor distinguishes real invocations (``python scripts/foo.py``,
# ``[doc](references/api.md)``, ``python ooxml/scripts/unpack.py``) from
# mention-only phrasing ("the scripts/ directory contains ..." has no
# extension and is non-actionable).
#
# Producer's ``has_scripts`` / ``has_references`` columns aren't enough —
# they record that the ingest dir *contained* those subdirs (~46% of
# rows). Roughly two-thirds of those never invoke the files from the
# body and don't need filesystem assets shipped.
import re as _re

_FS_FILE_REF = _re.compile(
    r"\b(?:scripts|references)/[\w./-]+\."
    r"(?:py|js|ts|sh|md|json|yaml|yml|txt|toml|cfg|css|html?|jsx|tsx"
    r"|ipynb|sql|rb|go|rs|java|swift|kt|csv|xml|env|lock|conf|ini)\b",
    _re.IGNORECASE,
)


def _needs_fs_assets(body: str | None) -> bool:
    """True iff the skill body uses the ``{baseDir}`` OpenSpace placeholder
    or names a specific file (with extension) under ``scripts/`` or
    ``references/``. Bare directory mentions ("the scripts/ folder"),
    sibling markdown links (``[viewmodels](viewmodels.md)``) and other
    discussion-only references return False — those skills don't reliably
    work in Route B's inline-injection model anyway.

    Body-only fallback used when the on-disk skill dir isn't available at
    export time (cross-machine export). When the dir *is* present, the
    export loop prefers ``_dir_referenced_assets`` which grounds the
    decision in the actual files and catches the many bare-relative /
    loose-file reference styles this regex misses."""
    if not body:
        return False
    if "{baseDir}" in body:
        return True
    return bool(_FS_FILE_REF.search(body))


# Files that carry no agent-actionable content; their presence alone never
# makes a skill "need" its directory shipped.
_JUNK_NAMES = frozenset({
    "SKILL.md", ".meta.json", "_meta.json", "meta.json", ".skill_id",
    "LICENSE", "LICENSE.txt", "LICENSE.md", "LICENCE", "LICENCE.txt",
    "README.md", "readme.md", ".gitignore", ".DS_Store",
})
# Build artifacts / caches — never authored attachments, skip when scanning.
_SKIP_DIRS = frozenset({
    "node_modules", "__pycache__", ".git", "dist", "build",
    ".pytest_cache", ".mypy_cache", "venv", ".venv", ".idea", ".vscode",
})


def _dir_referenced_assets(parent: Path, body: str) -> bool:
    """True if the skill dir holds real attachment files AND the body names
    at least one of them.

    Grounds the path decision in actual on-disk files instead of a fixed
    ``scripts|references`` regex, so the many real reference styles the
    regex misses are recognized:
      - attachment subdirs of any name (``references/`` ``resources/``
        ``examples/`` ``reference/`` ``indexes/`` ``rules/`` ``agents/`` …)
      - loose root-level files (``run.py`` ``technical-reference.md``)
      - files one level inside an attachment subdir (named by basename)

    Matching is deliberately specific to avoid false positives: a subdir
    must appear as ``name/`` or be backtick-wrapped (not the bare common
    word), and a file basename must be ≥5 chars (basenames carry an
    extension, so this is specific enough) and appear verbatim in the body.
    Returns on the first match (cheap; no full enumeration of large dirs)."""
    try:
        entries = list(parent.iterdir())
    except OSError:
        return False
    # First pass: top-level subdir references + loose root files.
    for e in entries:
        nm = e.name
        if nm in _JUNK_NAMES:
            continue
        if e.is_dir():
            if nm in _SKIP_DIRS:
                continue
            if (nm + "/") in body or ("`" + nm + "`") in body or ("`" + nm + "/") in body:
                return True
        elif e.is_file() and len(nm) >= 5 and nm in body:
            return True
    # Second pass: file basenames one level inside attachment subdirs.
    for e in entries:
        if e.is_dir() and e.name not in _SKIP_DIRS and e.name not in _JUNK_NAMES:
            try:
                for f in e.iterdir():
                    if (f.is_file() and len(f.name) >= 5
                            and f.name not in _JUNK_NAMES and f.name in body):
                        return True
            except OSError:
                pass
    return False


# Captures everything after ``{baseDir}/`` up to whitespace / common
# terminators (mirrors the consumer's render-time validator). Used to
# verify at export time that the producer dir actually contains the
# files the body claims. HelixDevelopment + parts of openclaw publish
# SKILL.md only — body refs are aspirational. We set path=NULL on
# those rows so the consumer treats them as bundle-less and strips
# the ``{baseDir}/`` prefix instead of rendering a misleading
# ``Skill directory`` header pointing at an empty dir.
_BASEDIR_REF = _re.compile(r"\{baseDir\}/(\S+?)(?=[\s)\'\"`]|$)")


def _any_ref_resolves(parent: Path, body: str) -> bool:
    """True if at least one body reference (``{baseDir}/<ref>`` or bare
    ``scripts/foo.ext`` / ``references/foo.ext``) actually exists under
    ``parent``. False when the body promises files that aren't there —
    caller leaves path=NULL so the consumer doesn't render a misleading
    Skill directory header."""
    refs: list[str] = []
    for r in _BASEDIR_REF.findall(body):
        refs.append(r.rstrip(".,;:"))
    for m in _FS_FILE_REF.finditer(body):
        refs.append(m.group(0))
    if not refs:
        # ``{baseDir}`` literal with no ``/<ref>`` (or regexes missed
        # everything). Default-trust: parent existing is enough signal.
        return True
    return any((parent / r).exists() for r in refs)


def _truthy_always(v) -> int:
    """Frontmatter ``always`` → 0/1, tolerant of the YAML string renderings
    third-party authors actually ship (``"True"``, ``yes``, ``on``, ``"1"``),
    not just the canonical ``True``/``"true"``/``1``."""
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return 1 if v == 1 else 0
    if isinstance(v, str):
        return 1 if v.strip().lower() in ("true", "yes", "1", "on") else 0
    return 0


def _decide_asset_path(assets_dir, stored_path, body: str, stats: dict) -> str | None:
    """Single source of truth for the consumer FS ``path`` decision, shared by
    the full export and the in-place update so they can't drift. Returns the
    SKILL.md path string when the skill needs its on-disk assets shipped, else
    None. Mutates ``stats`` counters (tolerant of missing keys)."""
    def bump(k):
        stats[k] = stats.get(k, 0) + 1
    candidate = (assets_dir / stored_path / "SKILL.md") if stored_path else None
    parent = candidate.parent if candidate else None
    if parent is None or not parent.exists():
        bump("needs_fs_dir_missing" if _needs_fs_assets(body) else "db_only")
        return None
    if "{baseDir}" in body:
        if _any_ref_resolves(parent, body):
            bump("needs_fs_with_path")
            return str(candidate)
        bump("needs_fs_refs_broken")
        return None
    if _dir_referenced_assets(parent, body):
        bump("needs_fs_with_path")
        return str(candidate)
    bump("db_only")
    return None


def _valid_emb_blob(emb_blob, embedding_dim: int) -> bool:
    """True iff the raw vec_skills blob matches the declared dim (dim*4 bytes
    for float32). A stale-dim blob (after a dim change / partial re-embed)
    stamped with the current dim ships a garbage/truncated vector to the
    consumer; callers drop it instead. Mirrors store.get_embedding's guard."""
    return bool(emb_blob) and len(emb_blob) == embedding_dim * 4


def export(
    src_dir: Path,
    dst_path: Path,
    *,
    limit: int | None = None,
    refresh_endpoint: str | None = None,
    assets_dir: Path | None = None,
    embedding_model: str | None = None,   # None → read from config.yaml embedding.model
    embedding_dim: int | None = None,     # None → config.yaml embedding.dim
) -> dict[str, int]:
    """Stream active producer rows → mass_library.db. Returns stats.

    ``assets_dir`` — root directory where the consumer will find each
    skill's auxiliary files (scripts/, references/, examples/). Producer
    writes ``row["path"] = <assets_dir>/<stored_path>/SKILL.md`` (absolute)
    so consumer's ``meta.path.parent`` is a real on-disk dir and
    ``{baseDir}`` substitutions resolve correctly. Defaults to
    ``src_dir`` (producer's own data dir) — works zero-config when
    producer + consumer share a filesystem; cross-machine deployment
    rsyncs ``<src_dir>/skills/`` to the consumer side and passes the
    consumer-side path here.
    """
    src_db = src_dir / "index.db"
    if not src_db.exists():
        raise FileNotFoundError(f"producer DB not found: {src_db}")

    # The embedding label/dim must match the consumer side, otherwise the runtime label-matching
    # fails and the embedding column is silently dropped → the mass pool degrades to BM25-only.
    # Read from config.yaml by default so it stays consistent with this repo's actual model
    # (no longer hard-coding a possibly-stale pipizhao label).
    if embedding_model is None or embedding_dim is None:
        _cfg_model, _cfg_dim = _config_embedding()
        if embedding_model is None:
            embedding_model = _cfg_model
        if embedding_dim is None:
            embedding_dim = _cfg_dim

    if assets_dir is None:
        assets_dir = src_dir
    assets_dir = assets_dir.resolve()

    # Load embeddings up-front
    embeddings = _load_embeddings_from_vec_skills(src_dir)
    embedding_dtype = "float32"

    # Prepare destination
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    # Fresh export — drop the old DB plus any WAL/SHM sidecars so a stale
    # consumer process (which may have the DB file held open) doesn't see
    # half-old / half-new state on disk while we rewrite.
    for suffix in ("", "-wal", "-shm"):
        side = dst_path.with_name(dst_path.name + suffix) if suffix else dst_path
        if side.exists():
            side.unlink()
    dst = sqlite3.connect(str(dst_path))
    dst.execute("PRAGMA journal_mode=WAL")
    dst.execute("PRAGMA synchronous=NORMAL")
    dst.executescript(_EVER_V2_SCHEMA)
    dst.commit()

    # Stream from src
    src = sqlite3.connect(str(src_db))
    src.row_factory = sqlite3.Row
    # ``active = 1`` gates on the producer-side license classification
    # (see source_license_report.csv → GREEN bucket). ``COALESCE(active, 1)``
    # is a backstop for older index.db snapshots that pre-date the column.
    q = """
        SELECT skill_id, name, source, description, body,
               frontmatter_raw, source_url, license, category, tags,
               quality_score, safety_flags, content_hash,
               body_tokens, has_scripts, has_references,
               added_at, updated_at, stored_path
        FROM skills
        WHERE deleted = 0 AND COALESCE(active, 1) = 1
    """
    if limit:
        q += f" LIMIT {int(limit)}"

    stats = {
        "total": 0,
        "with_embedding": 0,
        "without_embedding": 0,
        "needs_fs_with_path": 0,        # needs fs AND on-disk dir present AND refs resolve
        "needs_fs_dir_missing": 0,      # needs fs but stored_path dir is gone (data gap)
        "needs_fs_refs_broken": 0,      # dir present but no body ref resolves → path=NULL
        "db_only": 0,                   # body has no actionable fs reference → NULL path
        "always": 0,
        "with_requires": 0,
    }
    t0 = time.time()
    now = int(time.time())
    cur = dst.cursor()
    batch = []
    BATCH_SIZE = 500

    for row in src.execute(q):
        emb_blob = embeddings.get(row["skill_id"])
        # Map row → consumer INSERT tuple. The helper owns the producer
        # frontmatter parse (always/requires), the dir-grounded FS ``path``
        # decision, the stale-dim blob drop, the ``everclaw`` enrichment key,
        # and the with_embedding/always/needs_fs/emb_dim_mismatch stat
        # side-effects — so this loop only tracks the running ``total``.
        batch.append(_row_to_insert_tuple(
            row, emb_blob,
            assets_dir=assets_dir,
            embedding_model=embedding_model,
            embedding_dim=embedding_dim,
            embedding_dtype=embedding_dtype,
            stats=stats,
            now=now,
        ))
        stats["total"] += 1

        if len(batch) >= BATCH_SIZE:
            cur.executemany(INSERT_SQL, batch)
            dst.commit()
            batch.clear()
            if stats["total"] % 5000 == 0:
                rate = stats["total"] / (time.time() - t0)
                print(
                    f"  {stats['total']} rows ({rate:.0f}/s)",
                    flush=True,
                )

    if batch:
        cur.executemany(INSERT_SQL, batch)
        dst.commit()

    src.close()
    dst.close()

    # Write refresh-endpoint sentinel for consumer auto-discovery
    if refresh_endpoint:
        endpoint_file = dst_path.parent / ".refresh_endpoint"
        endpoint_file.write_text(refresh_endpoint.strip() + "\n", encoding="utf-8")
        print(f"wrote {endpoint_file} = {refresh_endpoint}")

    # Write .stale marker — consumer's next start re-attaches the updated DB
    stale_file = dst_path.parent / ".stale"
    stale_file.write_text("dirty\n", encoding="utf-8")
    print(f"wrote {stale_file}")

    return stats


def _safe_json(s: str | None):
    if not s:
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None


# ════════ incremental update — update the existing mass pool in place (no full re-export) ════════
def _producer_active_rows(src_db: Path):
    """Yield producer rows with ``active=1 AND deleted=0`` as sqlite3.Row.

    Joins ``quality_judgments`` (keyed by content_hash) so each row also
    carries the 3-facet ``subscores`` JSON (``{"utility": ...,
    "robustness": ..., "safety": ..., "flags": [...]}``). The consumer
    needs these for downstream per-facet filtering / analysis; without
    them only the composite ``quality_score`` is visible on the consumer
    side, which is the shape of the data v6 had pre-2026-05-28.

    Same active-row WHERE clause as ``export_to_mass_library.export()``
    so the two pipelines agree on what 'active' means.
    """
    conn = sqlite3.connect(str(src_db))
    conn.row_factory = sqlite3.Row
    sql = """
        SELECT s.skill_id, s.name, s.source, s.description, s.body,
               s.frontmatter_raw, s.source_url, s.license, s.category,
               s.tags, s.quality_score, s.safety_flags, s.content_hash,
               s.body_tokens, s.has_scripts, s.has_references,
               s.added_at, s.updated_at, s.stored_path,
               q.subscores AS subscores_json
        FROM skills s
        LEFT JOIN quality_judgments q ON q.content_hash = s.content_hash
        WHERE s.deleted = 0 AND COALESCE(s.active, 1) = 1
    """
    yield from conn.execute(sql)
    conn.close()


def _row_to_insert_tuple(
    row, emb_blob: bytes | None, *,
    assets_dir: Path,
    embedding_model: str,
    embedding_dim: int,
    embedding_dtype: str,
    stats: dict,
    now: int,
    count_stats: bool = True,
):
    """Map a producer SkillRecord row → consumer ``skills`` INSERT tuple.

    Mirrors the mapping inside ``export_to_mass_library.export()``; kept
    in a small helper here so the in-place update path doesn't drift.
    """
    orig_fm = _safe_json(row["frontmatter_raw"]) or {}
    is_always = _truthy_always(orig_fm.get("always"))
    requires = orig_fm.get("requires") or {}
    if not isinstance(requires, dict):
        requires = {}
    requires_json = json.dumps(requires, ensure_ascii=False, default=str)

    # Shared FS-path decision (same helper as full export) — previously this
    # used a body-only regex and skipped export's dir-grounded ~6.5K bare-ref
    # fix, so a skill's `path` depended on which sync last touched it.
    # When count_stats is False (UPDATE pass) route stat side-effects to a
    # throwaway dict so the same row isn't counted twice (insert + update).
    asset_stats = stats if count_stats else {}
    path_value = _decide_asset_path(assets_dir, row["stored_path"],
                                    row["body"] or "", asset_stats)

    # Drop a stale-dim blob (mirror export / store.get_embedding) so we never
    # stamp a garbage vector with the current dim label.
    if emb_blob is not None and not _valid_emb_blob(emb_blob, embedding_dim):
        if count_stats:
            stats["emb_dim_mismatch"] = stats.get("emb_dim_mismatch", 0) + 1
        emb_blob = None

    # Decompose the 3-facet subscores into top-level keys so consumer
    # code can read them without parsing nested JSON. ``subscores_json``
    # is the raw column from quality_judgments; shape is
    # ``{"utility": int, "robustness": int, "safety": int, "flags": [...]}``.
    subscores = _safe_json(row["subscores_json"]) if (
        "subscores_json" in row.keys() and row["subscores_json"]
    ) else None

    everclaw_meta = {
        "skill_id": row["skill_id"],
        "source": row["source"],
        "category": row["category"],
        "quality_score": row["quality_score"],
        "tags": _safe_json(row["tags"]),
        "safety_flags": _safe_json(row["safety_flags"]),
        "has_scripts": bool(row["has_scripts"]),
        "has_references": bool(row["has_references"]),
        "body_tokens": row["body_tokens"],
        "source_url": row["source_url"],
        "license": row["license"],
        "added_at": row["added_at"],
    }
    if subscores:
        # Preserve the producer-side names u/r/s/flags verbatim under a
        # dedicated ``subscores`` key. Composite ``quality_score`` is
        # already exposed at the same level for back-compat.
        everclaw_meta["subscores"] = {
            "utility":    subscores.get("utility"),
            "robustness": subscores.get("robustness"),
            "safety":     subscores.get("safety"),
            "flags":      subscores.get("flags") or [],
        }

    orig_fm["everclaw"] = everclaw_meta
    fm_json = json.dumps(orig_fm, ensure_ascii=False, default=str)

    created_ts = _parse_added_at(row["added_at"]) or now
    updated_ts = _parse_added_at(row["updated_at"]) or now

    if count_stats:
        if emb_blob:
            stats["with_embedding"] += 1
        else:
            stats["without_embedding"] += 1
        if is_always:
            stats["always"] += 1
        if requires:
            stats["with_requires"] += 1

    return (
        row["name"],
        row["source"],
        row["description"] or "",
        row["body"],
        path_value,
        is_always,
        requires_json,
        fm_json,
        row["license"],
        row["category"],
        row["content_hash"],
        emb_blob,  # bytes or None
        embedding_model if emb_blob else None,
        embedding_dim if emb_blob else None,
        embedding_dtype if emb_blob else None,
        None,  # imported_at
        created_ts,
        updated_ts,
    )


INSERT_SQL = """
INSERT INTO skills (
    name, source, description, body, path,
    is_always, requires_json, frontmatter_json,
    scope, license, category,
    content_hash, embedding, embedding_model,
    embedding_dim, embedding_dtype,
    imported_at, created_at, updated_at,
    confidence
) VALUES (
    ?, ?, ?, ?, ?,
    ?, ?, ?,
    NULL, ?, ?,
    ?, ?, ?,
    ?, ?,
    ?, ?, ?,
    1.0
)
"""


def sync(
    src_dir: Path,
    dst_path: Path,
    *,
    update_existing: bool = False,
    assets_dir: Path | None = None,
    embedding_model: str | None = None,   # None → config.yaml embedding.model (consistent with consumer)
    embedding_dim: int | None = None,
    dry_run: bool = False,
    do_backup: bool = True,
) -> dict[str, int]:
    """Apply the producer→consumer delta to ``dst_path`` in place.

    Returns a stats dict with insert / delete counts and the row-mapping
    sub-stats from ``_row_to_insert_tuple``.
    """
    if embedding_model is None or embedding_dim is None:
        _m, _d = _config_embedding()
        embedding_model = embedding_model or _m
        embedding_dim = embedding_dim or _d
    src_db = src_dir / "index.db"
    if not src_db.exists():
        raise FileNotFoundError(f"producer DB not found: {src_db}")
    if not dst_path.exists():
        raise FileNotFoundError(
            f"target consumer DB not found: {dst_path}. "
            f"Use export_to_mass_library.py to create it first."
        )

    if assets_dir is None:
        assets_dir = src_dir
    assets_dir = assets_dir.resolve()

    # Backup before any destructive op (default ON).
    if do_backup and not dry_run:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup_path = dst_path.with_name(f"{dst_path.name}.backup-{ts}")
        print(f"backing up {dst_path.name} → {backup_path.name}", flush=True)
        shutil.copy2(dst_path, backup_path)

    # Load producer embeddings (sqlite-vec canonical store).
    embeddings = _load_embeddings_from_vec_skills(src_dir)

    # Snapshot consumer content_hash set.
    print(f"reading existing rows in {dst_path.name}...", flush=True)
    dst = sqlite3.connect(str(dst_path))
    dst.execute("PRAGMA journal_mode=WAL")
    dst.execute("PRAGMA synchronous=NORMAL")
    existing_hashes = {
        h for (h,) in dst.execute("SELECT content_hash FROM skills")
    }
    print(f"  consumer rows: {len(existing_hashes)}", flush=True)

    # Walk producer once to: (a) build the active-hash set, (b) cache
    # rows so we can map deltas without a second pass.
    print(f"scanning producer active rows...", flush=True)
    active_rows = []
    active_hashes = set()
    for row in _producer_active_rows(src_db):
        active_rows.append(dict(row))
        active_hashes.add(row["content_hash"])
    print(f"  producer active: {len(active_hashes)}", flush=True)

    to_delete = existing_hashes - active_hashes
    to_insert_hashes = active_hashes - existing_hashes
    common = existing_hashes & active_hashes

    print(f"\ndelta summary:", flush=True)
    print(f"  in both (untouched):  {len(common):>7}", flush=True)
    print(f"  DELETE (now inactive): {len(to_delete):>7}", flush=True)
    print(f"  INSERT (new active):  {len(to_insert_hashes):>7}", flush=True)
    if update_existing:
        print(f"  UPDATE existing:       {len(common):>7} (--update-existing)",
              flush=True)
    expected = len(common) + len(to_insert_hashes)
    print(f"  expected final count: {expected}\n", flush=True)

    if dry_run:
        print("(dry-run: no writes)", flush=True)
        return {
            "deleted": len(to_delete),
            "inserted": 0,
            "expected_final": expected,
        }

    stats = {
        "deleted": 0,
        "inserted": 0,
        "with_embedding": 0,
        "without_embedding": 0,
        "needs_fs_with_path": 0,
        "needs_fs_dir_missing": 0,
        "needs_fs_refs_broken": 0,
        "db_only": 0,
        "always": 0,
        "with_requires": 0,
        "expected_final": expected,
    }

    cur = dst.cursor()
    now = int(time.time())

    # DELETE pass (chunked to avoid huge IN clauses).
    if to_delete:
        print(f"deleting {len(to_delete)} rows...", flush=True)
        del_list = list(to_delete)
        CHUNK = 500
        for i in range(0, len(del_list), CHUNK):
            chunk = del_list[i : i + CHUNK]
            placeholders = ",".join(["?"] * len(chunk))
            cur.execute(
                f"DELETE FROM skills WHERE content_hash IN ({placeholders})",
                chunk,
            )
        stats["deleted"] = cur.rowcount if cur.rowcount >= 0 else len(to_delete)
        dst.commit()

    # INSERT pass.
    if to_insert_hashes:
        print(f"inserting {len(to_insert_hashes)} rows...", flush=True)
        batch = []
        BATCH_SIZE = 500
        t0 = time.time()
        for prow in active_rows:
            if prow["content_hash"] not in to_insert_hashes:
                continue
            emb_blob = embeddings.get(prow["skill_id"])
            batch.append(_row_to_insert_tuple(
                prow, emb_blob,
                assets_dir=assets_dir,
                embedding_model=embedding_model,
                embedding_dim=embedding_dim,
                embedding_dtype="float32",
                stats=stats,
                now=now,
            ))
            stats["inserted"] += 1
            if len(batch) >= BATCH_SIZE:
                cur.executemany(INSERT_SQL, batch)
                dst.commit()
                if stats["inserted"] % 5000 == 0:
                    rate = stats["inserted"] / max(time.time() - t0, 0.001)
                    print(f"  {stats['inserted']}/{len(to_insert_hashes)} "
                          f"({rate:.0f}/s)", flush=True)
                batch = []
        if batch:
            cur.executemany(INSERT_SQL, batch)
            dst.commit()
        elapsed = time.time() - t0
        print(f"  inserted {stats['inserted']} in {elapsed:.0f}s", flush=True)

    # Optional UPDATE pass for rows in both sides (metadata refresh).
    if update_existing and common:
        print(f"refreshing metadata on {len(common)} existing rows "
              "(--update-existing)...", flush=True)
        common_list = list(common)
        prod_by_hash = {r["content_hash"]: r for r in active_rows
                        if r["content_hash"] in common}
        updated = 0
        for hash_ in common_list:
            prow = prod_by_hash.get(hash_)
            if prow is None:
                continue
            emb_blob = embeddings.get(prow["skill_id"])
            tup = _row_to_insert_tuple(
                prow, emb_blob,
                assets_dir=assets_dir,
                embedding_model=embedding_model,
                embedding_dim=embedding_dim,
                embedding_dtype="float32",
                stats=stats,
                now=now,
                count_stats=False,  # UPDATE pass: don't re-count insert-pass stats
            )
            # tuple field order matches INSERT_SQL; map back to UPDATE.
            (name, source, description, body, path_value,
             is_always, requires_json, fm_json,
             license_, category, content_hash,
             emb, emb_model, emb_dim, emb_dtype,
             _imported_at, _created_ts, updated_ts) = tup
            cur.execute(
                """
                UPDATE skills SET
                    name = ?, source = ?, description = ?, body = ?,
                    path = ?, is_always = ?, requires_json = ?,
                    frontmatter_json = ?, license = ?, category = ?,
                    embedding = ?, embedding_model = ?, embedding_dim = ?,
                    embedding_dtype = ?, updated_at = ?
                WHERE content_hash = ?
                """,
                (name, source, description, body, path_value,
                 is_always, requires_json, fm_json,
                 license_, category,
                 emb, emb_model, emb_dim, emb_dtype, updated_ts,
                 content_hash),
            )
            updated += 1
            if updated % 5000 == 0:
                dst.commit()
                print(f"  updated {updated}/{len(common_list)}", flush=True)
        dst.commit()
        stats["updated_existing"] = updated

    # Final verification.
    final_count = dst.execute("SELECT COUNT(*) FROM skills").fetchone()[0]
    stats["final_count"] = final_count
    dst.close()

    print(f"\ndone. mass_library now has {final_count} rows "
          f"(expected {expected}).", flush=True)
    if final_count != expected:
        print(f"!! WARNING: final count {final_count} != expected {expected}",
              file=sys.stderr)

    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC,
                    help=f"producer data dir (default: {DEFAULT_SRC})")
    ap.add_argument("--dst", type=Path, default=DEFAULT_DST,
                    help=f"output mass_library.db (default: {DEFAULT_DST})")
    ap.add_argument("--limit", type=int, default=None,
                    help="only export the first N rows (debug)")
    ap.add_argument("--refresh-endpoint", default=None,
                    help="write this URL to <dst>/.refresh_endpoint so "
                         "consumers using the mirror auto-discover the "
                         "producer refresh service (eg. 'http://producer-host:8765')")
    ap.add_argument("--assets-dir", type=Path, default=None,
                    help="absolute filesystem root the consumer will see "
                         "for scripts/references — used to build the path "
                         "column so {baseDir} substitutions resolve. "
                         "Defaults to --src (works zero-config on same machine).")
    ap.add_argument("--embedding-model", default=None,
                    help="embedding model name stamped on each row (must match the consumer's "
                         "config.embedding_model, otherwise the runtime silently drops the embedding). "
                         "Read from config.yaml embedding.model by default")
    ap.add_argument("--embedding-dim", type=int, default=None,
                    help="read from config.yaml embedding.dim by default")
    ap.add_argument("--incremental", action="store_true",
                    help="incrementally update the existing mass_library.db (diff by content_hash to "
                         "insert new rows / delete deactivated rows), rather than a full rewrite")
    ap.add_argument("--update-existing", action="store_true",
                    help="(with --incremental) also refresh the metadata of rows present on both sides")
    ap.add_argument("--no-backup", action="store_true",
                    help="(with --incremental) skip the .backup-<ts> sidecar")
    ap.add_argument("--dry-run", action="store_true",
                    help="(with --incremental) only print the delta, don't write")
    args = ap.parse_args()

    # --- incremental mode: go through sync() to update the existing mass pool in place, then return ---
    if args.incremental:
        assets = args.assets_dir or args.src
        stats = sync(
            Path(args.src).resolve(), Path(args.dst).resolve(),
            update_existing=args.update_existing,
            assets_dir=Path(assets).resolve() if assets else None,
            dry_run=args.dry_run, do_backup=not args.no_backup,
        )
        print(f"\nstats: {json.dumps(stats, indent=2)}")
        return 0

    # Resolve the embedding defaults (same logic as inside export(); computed here up-front for printing)
    _cfg_model, _cfg_dim = _config_embedding()
    emb_model = args.embedding_model or _cfg_model
    emb_dim = args.embedding_dim or _cfg_dim

    assets_dir = args.assets_dir or args.src
    print(f"src:         {args.src}")
    print(f"dst:         {args.dst}")
    print(f"assets-dir:  {assets_dir}  (consumer-side absolute path for {{baseDir}})")
    print(f"embedding:   {emb_model} dim={emb_dim}"
          f"{'  (from config.yaml)' if args.embedding_model is None else '  (--embedding-model)'}")
    print()

    t0 = time.time()
    stats = export(
        args.src, args.dst,
        limit=args.limit,
        refresh_endpoint=args.refresh_endpoint,
        assets_dir=assets_dir,
        embedding_model=emb_model,
        embedding_dim=emb_dim,
    )
    elapsed = time.time() - t0

    print()
    print("=" * 60)
    print("Export complete")
    print("=" * 60)
    print(f"  elapsed:                  {elapsed:.0f}s")
    print(f"  rows exported:            {stats['total']}")
    print(f"  with embedding:           {stats['with_embedding']}")
    print(f"  without embedding:        {stats['without_embedding']}")
    print(f"  always=true (is_always):  {stats['always']}")
    print(f"  with requires deps:       {stats['with_requires']}")
    print()
    print("  fs assets attachment:")
    print(f"    needs_fs, path filled:  {stats['needs_fs_with_path']}")
    print(f"    needs_fs, dir MISSING:  {stats['needs_fs_dir_missing']} "
          f"(body references fs but {assets_dir}/<stored_path> is gone)")
    print(f"    needs_fs, refs broken:  {stats['needs_fs_refs_broken']} "
          f"(dir exists but no body ref resolves — bundle never published; path=NULL)")
    print(f"    db-only:                {stats['db_only']}")
    print()
    print(f"  output size:              {args.dst.stat().st_size / 1024 / 1024:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
