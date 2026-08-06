"""core.store — SkillStore: the SQLite-backed skill store (schema, CRUD, vector search)."""
from __future__ import annotations

from datetime import date, datetime, timezone

from .models import SCHEMA_SQL, VEC_TABLE_NAME, SkillRecord


# =============================================================
# Storage layer (originally skillcorpus/storage.py, now merged here)
# =============================================================
#
# Storage layer: SQLite (metadata) + sqlite-vec (vectors, source of truth) + filesystem (source files).
#
# Adapted from OpenSpace openspace/skill_engine/store.py:
#   - drops the evolution_suggestions / execution_analyses / tool_quality tables
#   - keeps the skills table + vector index
#   - synchronous (sync) interface, not async
#
# Vector retrieval path (optimized 2026-04-29):
#   - writes still go through sqlite-vec (the vec_skills table is the source of truth, never lost)
#   - reads (vector_search / find_near_duplicates) prefer the faiss HNSW index
#     (measured on 80K rows, cosine top-5: sqlite-vec 800ms → faiss 0.4ms, ~2000x)
#   - the faiss index files ``<db_dir>/skill_index.faiss`` + ``skill_index_ids.json``
#     are rebuilt automatically from vec_skills when missing; long-term accumulated drift can be manually fixed with rebuild_faiss_index()

import json
import logging
import shutil
import sqlite3
import struct
import threading
from pathlib import Path
from typing import Any

import sqlite_vec


# faiss is a hard dependency (dedup nearest-neighbor search uses it, there is no "faiss not installed" soft-degrade path).
import faiss
import numpy as np


def _json_default(o: Any) -> Any:
    """Fallback JSON serializer — handle date/datetime/Path/other."""
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, Path):
        return str(o)
    return str(o)


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=_json_default)

logger = logging.getLogger("skillcorpus.store")


class SkillStore:
    """SQLite + sqlite-vec persistence."""

    def __init__(self, db_path: Path, embedding_dim: int = 1536):
        self.db_path = Path(db_path)
        self.embedding_dim = embedding_dim
        # Thread-local SQLite connections: sharing one conn across threads
        # with `check_same_thread=False` triggers "bad parameter or other
        # API misuse" when two threads' implicit cursors overlap. Giving
        # each thread its own conn sidesteps the issue and SQLite WAL
        # mode handles concurrent writers via row-level locks.
        self._local = threading.local()
        self._all_conns: list[sqlite3.Connection] = []   # for close()
        self._conns_lock = threading.Lock()
        # faiss read-side index (lazy loaded, shared across threads)
        self._faiss_index: "faiss.Index | None" = None
        self._faiss_ids: list[str] | None = None
        self._faiss_id_set: set[str] = set()   # membership mirror of _faiss_ids
        self._faiss_pending_inserts: int = 0   # incremental adds since last full rebuild
        # Full rebuild once incremental drift (incl. REPLACE/delete churn that
        # incremental add can't reflect) crosses this many adds.
        self._faiss_rebuild_threshold: int = 500
        self._faiss_lock = threading.Lock()
        # License whitelist — sources whose upstream LICENSE is a GREEN
        # (commercially-deployable) permissive license. Newly inserted
        # skills from any other source get ``active=0`` and are skipped
        # by ``export.py`` (which gates on
        # ``active=1``). Loaded lazily on first ``insert()`` call so
        # store construction stays cheap.

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        # WAL lets readers run concurrently but writers stay mutually
        # exclusive at the DB level. Default busy_timeout=0 means
        # competing writers fail instantly with "database is locked"
        # — under 8-thread outer parallelism that produced ~75%
        # failure rate. 30s gives writer handoff plenty of room
        # while keeping a real deadline if something is genuinely wedged.
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        self._local.conn = conn
        with self._conns_lock:
            self._all_conns.append(conn)
        return conn

    def init_schema(self) -> None:
        """Create all tables (idempotent) + run any necessary small migrations."""
        conn = self._connect()
        conn.executescript(SCHEMA_SQL)
        conn.execute(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS {VEC_TABLE_NAME} USING vec0(
                skill_id TEXT PRIMARY KEY,
                embedding FLOAT[{self.embedding_dim}]
            )
            """
        )
        conn.commit()

    def close(self) -> None:
        # Close every thread-local conn we know about (best-effort).
        with self._conns_lock:
            for c in self._all_conns:
                try:
                    c.close()
                except Exception:
                    pass
            self._all_conns.clear()
        if hasattr(self._local, "conn"):
            self._local.conn = None

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def insert(self, rec: SkillRecord, embedding: list[float] | None = None) -> None:
        # active is not a store concern: a row lands inactive (excluded from
        # export) and curate.license_audit flips it per the GREEN whitelist.
        active = 0

        # Strip lone UTF-16 surrogates that the sqlite3 driver can't bind
        # (UnicodeEncodeError "surrogates not allowed"). Real text rarely
        # contains these — they're typically from upstream files mis-decoded
        # with errors='surrogateescape'. Replace with U+FFFD.
        def _sanitize(s):
            if not isinstance(s, str):
                return s
            try:
                s.encode("utf-8")
                return s
            except UnicodeEncodeError:
                logger.warning(
                    "UTF-8 surrogate sanitized in skill text (sample: %r)",
                    s[:80],
                )
                return s.encode("utf-8", "replace").decode("utf-8")

        params = (
            rec.skill_id, _sanitize(rec.name), _sanitize(rec.description),
            _sanitize(rec.body),
            _sanitize(_json_dumps(rec.frontmatter_raw)),
            rec.source, rec.source_url, _sanitize(rec.source_path), rec.license,
            rec.content_hash, rec.name_hash, rec.category,
            _json_dumps(rec.tags),
            rec.quality_score,
            _json_dumps(rec.safety_flags),
            rec.body_tokens,
            int(rec.has_scripts), int(rec.has_references),
            int(rec.deleted), rec.superseded_by,
            rec.added_at, rec.updated_at, rec.stored_path,
            active,
        )
        sql = """
            INSERT OR REPLACE INTO skills
            (skill_id, name, description, body, frontmatter_raw, source, source_url,
             source_path, license, content_hash, name_hash, category, tags,
             quality_score, safety_flags, body_tokens, has_scripts, has_references,
             deleted, superseded_by, added_at, updated_at, stored_path, active)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """
        # Retry on transient SQLite write contention. busy_timeout=30s waits
        # passively; explicit backoff retries handle the rare cases where
        # the wait still fires (e.g. very long-running peer transaction
        # or contention from external connections). Without this the
        # 2026-05-20 540K-skill batch dropped 152K rows silently.
        import time
        last_err = None
        conn = None
        for attempt in range(8):
            try:
                conn = self._connect()
                conn.execute(sql, params)
                if embedding is not None:
                    self._upsert_vector(rec.skill_id, embedding)
                conn.commit()
                if embedding is not None:
                    # Keep the in-memory faiss index current so near-dup
                    # detection within this same process can match against
                    # rows inserted earlier in the run (see _faiss_note_insert).
                    self._faiss_note_insert(rec.skill_id, embedding)
                return
            except sqlite3.OperationalError as e:
                # Roll back so a failed DML doesn't leave this thread-local
                # connection mid-transaction — a later insert's anti-clobber
                # SELECT would otherwise read a stale open-txn snapshot.
                try:
                    conn.rollback()
                except Exception:
                    pass
                if "locked" not in str(e).lower():
                    raise
                last_err = e
                time.sleep(min(0.25 * (2 ** attempt), 8.0))
        # All 8 attempts failed with "locked" — surface the last error.
        assert last_err is not None, "unreachable: range(8) always runs"
        raise last_err

    def supersede(self, loser_id: str, winner_id: str) -> bool:
        """Mark loser_id as merged and superseded by winner_id (soft delete + superseded_by)."""
        conn = self._connect()
        from datetime import datetime, timezone
        cur = conn.execute(
            """UPDATE skills
               SET deleted = 1, superseded_by = ?, updated_at = ?
               WHERE skill_id = ?""",
            (winner_id, datetime.now(timezone.utc).isoformat(), loser_id),
        )
        conn.commit()
        return cur.rowcount > 0

    def _upsert_vector(self, skill_id: str, embedding: list[float]) -> None:
        conn = self._connect()
        conn.execute(f"DELETE FROM {VEC_TABLE_NAME} WHERE skill_id = ?", (skill_id,))
        conn.execute(
            f"INSERT INTO {VEC_TABLE_NAME}(skill_id, embedding) VALUES (?, ?)",
            (skill_id, sqlite_vec.serialize_float32(embedding)),
        )
        conn.commit()

    def get_embedding(self, skill_id: str) -> list[float] | None:
        """Return the L2-normalized embedding for ``skill_id``, or ``None`` if
        no vector is stored. Used by dedup name-hash path to compute the
        real cosine vs candidate (instead of relying on the cos=1.0
        placeholder, which historically caused ~7K cross-source false-positive
        merges where same name + different content tripped the auto-merge
        gate).
        """
        conn = self._connect()
        row = conn.execute(
            f"SELECT embedding FROM {VEC_TABLE_NAME} WHERE skill_id = ?",
            (skill_id,),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        raw = row[0]
        if len(raw) != self.embedding_dim * 4:
            return None
        return list(struct.unpack(f"{self.embedding_dim}f", raw))

    def get(self, skill_id: str) -> SkillRecord | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM skills WHERE skill_id = ? AND deleted = 0",
            (skill_id,),
        ).fetchone()
        return self._row_to_record(row) if row else None

    def get_by_content_hash(self, content_hash: str) -> SkillRecord | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM skills WHERE content_hash = ? AND deleted = 0 LIMIT 1",
            (content_hash,),
        ).fetchone()
        return self._row_to_record(row) if row else None

    def get_by_name_hash(self, name_hash: str) -> list[SkillRecord]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM skills WHERE name_hash = ? AND deleted = 0",
            (name_hash,),
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def list(
        self,
        category: str | None = None,
        source: str | None = None,
        tag: str | None = None,
        min_quality: float = 0.0,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SkillRecord]:
        conn = self._connect()
        sql = "SELECT * FROM skills WHERE deleted = 0"
        params: list[Any] = []
        if category:
            sql += " AND category = ?"
            params.append(category)
        if source:
            sql += " AND source = ?"
            params.append(source)
        if min_quality > 0:
            sql += " AND quality_score >= ?"
            params.append(min_quality)
        if tag:
            sql += " AND tags LIKE ?"
            params.append(f'%"{tag}"%')
        sql += " ORDER BY quality_score DESC, added_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(sql, params).fetchall()
        return [self._row_to_record(r) for r in rows]

    def list_all(self, include_deleted: bool = False) -> list[SkillRecord]:
        conn = self._connect()
        sql = "SELECT * FROM skills"
        if not include_deleted:
            sql += " WHERE deleted = 0"
        rows = conn.execute(sql).fetchall()
        return [self._row_to_record(r) for r in rows]

    def update(self, skill_id: str, **fields: Any) -> SkillRecord | None:
        rec = self.get(skill_id)
        if rec is None:
            return None
        for k, v in fields.items():
            if hasattr(rec, k):
                setattr(rec, k, v)
        from datetime import datetime, timezone
        rec.updated_at = datetime.now(timezone.utc).isoformat()
        self.insert(rec)
        return rec

    def delete(self, skill_id: str, soft: bool = True) -> bool:
        conn = self._connect()
        if soft:
            from datetime import datetime, timezone
            cur = conn.execute(
                "UPDATE skills SET deleted = 1, updated_at = ? WHERE skill_id = ?",
                (datetime.now(timezone.utc).isoformat(), skill_id),
            )
        else:
            cur = conn.execute("DELETE FROM skills WHERE skill_id = ?", (skill_id,))
            conn.execute(f"DELETE FROM {VEC_TABLE_NAME} WHERE skill_id = ?", (skill_id,))
        conn.commit()
        return cur.rowcount > 0

    def count(self) -> int:
        conn = self._connect()
        return conn.execute("SELECT COUNT(*) FROM skills WHERE deleted = 0").fetchone()[0]

    def stats(self) -> dict[str, Any]:
        conn = self._connect()
        total = conn.execute("SELECT COUNT(*) FROM skills WHERE deleted = 0").fetchone()[0]
        by_src = {
            r["source"]: r["n"]
            for r in conn.execute(
                "SELECT source, COUNT(*) AS n FROM skills WHERE deleted = 0 GROUP BY source"
            ).fetchall()
        }
        by_cat = {
            r["category"]: r["n"]
            for r in conn.execute(
                "SELECT category, COUNT(*) AS n FROM skills WHERE deleted = 0 GROUP BY category"
            ).fetchall()
        }
        avg_q = conn.execute(
            "SELECT AVG(quality_score) FROM skills WHERE deleted = 0"
        ).fetchone()[0] or 0.0
        return {
            "total": total,
            "by_source": by_src,
            "by_category": by_cat,
            "avg_quality": round(avg_q, 3),
        }

    # ------------------------------------------------------------------
    # FAISS read-side index (lazy load, source of truth stays in vec_skills)
    # ------------------------------------------------------------------

    @property
    def _faiss_path(self) -> Path:
        return self.db_path.parent / "skill_index.faiss"

    @property
    def _faiss_ids_path(self) -> Path:
        return self.db_path.parent / "skill_index_ids.json"

    def _faiss_load_or_build(self) -> bool:
        """Lazy load faiss index from disk; build from vec_skills if missing.

        Returns ``True`` when an index is available, ``False`` if faiss is
        not installed or ``vec_skills`` is empty.
        """
        if self._faiss_index is not None:
            return True
        # Try load from disk
        if self._faiss_path.exists() and self._faiss_ids_path.exists():
            try:
                self._faiss_index = faiss.read_index(str(self._faiss_path))
                self._faiss_ids = json.loads(
                    self._faiss_ids_path.read_text("utf-8"),
                )
                self._faiss_id_set = set(self._faiss_ids)
                logger.info(
                    "loaded faiss index: %d vectors", self._faiss_index.ntotal,
                )
                return True
            except Exception:
                logger.warning("faiss index load failed, will rebuild", exc_info=True)
                self._faiss_index = None
                self._faiss_ids = None
                self._faiss_id_set = set()
        # Build from vec_skills. Caller (_faiss_search) already holds
        # _faiss_lock, so use the unlocked variant — the lock is non-reentrant.
        return self._rebuild_faiss_index_locked()

    def rebuild_faiss_index(self) -> bool:
        """Dump all vec_skills embeddings into a fresh HNSW index, persist.

        Call after large ingest batches or whenever the on-disk faiss file
        looks stale (it lags behind sqlite-vec until rebuild). Returns
        ``True`` on success, ``False`` when faiss missing or no data.

        Public entry point — takes ``_faiss_lock`` so a direct/operator call
        (or scripts like rescan_dedup) can't run ``index.add`` concurrently
        with a ``_faiss_search`` (faiss HNSW is not concurrent-safe).
        """
        with self._faiss_lock:
            return self._rebuild_faiss_index_locked()

    def _rebuild_faiss_index_locked(self) -> bool:
        """Rebuild body. Caller MUST already hold ``_faiss_lock``."""
        conn = self._connect()
        rows = conn.execute(
            f"SELECT skill_id, embedding FROM {VEC_TABLE_NAME}",
        ).fetchall()
        if not rows:
            return False

        # Collect kept rows in lockstep — appending to both lists in the same
        # iteration guarantees ids[k] ↔ vectors[k]. (The old code indexed a
        # pre-zeroed array by the *raw* loop index and truncated the tail, so
        # any skipped malformed/empty embedding desynced every id past it.)
        ids: list[str] = []
        vec_list: list[tuple[float, ...]] = []
        for r in rows:
            emb = r["embedding"]
            if not emb or len(emb) != self.embedding_dim * 4:
                continue
            ids.append(r["skill_id"])
            vec_list.append(struct.unpack(f"{self.embedding_dim}f", emb))
        vectors = np.asarray(vec_list, dtype=np.float32) if vec_list else \
            np.zeros((0, self.embedding_dim), dtype=np.float32)
        if not ids:
            return False

        # Cosine via inner product on L2-normalized vectors
        faiss.normalize_L2(vectors)
        index = faiss.IndexHNSWFlat(self.embedding_dim, 32)
        index.hnsw.efConstruction = 80
        index.hnsw.efSearch = 50
        index.add(vectors)

        # Persist
        self._faiss_path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(index, str(self._faiss_path))
        self._faiss_ids_path.write_text(json.dumps(ids), "utf-8")

        self._faiss_index = index
        self._faiss_ids = ids
        self._faiss_id_set = set(ids)
        self._faiss_pending_inserts = 0
        logger.info(
            "rebuilt faiss index: %d vectors → %s",
            index.ntotal, self._faiss_path,
        )
        return True

    def _faiss_note_insert(self, skill_id: str, embedding: list[float]) -> None:
        """Sync a freshly-upserted vector into the in-memory faiss index.

        The faiss index is lazily loaded once and otherwise only refreshed by
        a full ``rebuild_faiss_index()``. Without this hook, rows inserted
        after that first load are invisible to faiss, so near-dup detection
        during a batch ingest silently can't match two new skills against
        each other (the ``_faiss_pending_inserts`` counter that was meant to
        guard this never incremented).

        Cheap path: add the (normalized) vector incrementally. Safety valve:
        once accumulated drift — including REPLACE/delete churn that
        incremental add can't reflect — crosses the threshold, do a full
        rebuild from ``vec_skills`` (the source of truth).
        """
        with self._faiss_lock:
            # Not loaded yet → the eventual lazy build reads vec_skills,
            # which already contains this committed row. Nothing to do.
            if self._faiss_index is None or self._faiss_ids is None:
                return
            self._faiss_pending_inserts += 1
            if self._faiss_pending_inserts >= self._faiss_rebuild_threshold:
                # Already holding _faiss_lock → unlocked rebuild (non-reentrant).
                self._rebuild_faiss_index_locked()  # resets pending to 0 on success
                # Also reset if rebuild returned False (e.g. all rows malformed),
                # else every subsequent insert would re-trigger a full rebuild.
                self._faiss_pending_inserts = 0
                return
            if skill_id in self._faiss_id_set:
                # Re-insert / overwrite (INSERT OR REPLACE, update(), re-embed):
                # HNSW has no delete, and a second add() would leave a duplicate
                # id pointing at a stale vector → near-dup queries return the id
                # twice / match the old embedding. Skip the incremental add; the
                # pending counter still advances so the periodic full rebuild
                # refreshes this id's vector from vec_skills.
                return
            vec = np.asarray([embedding], dtype=np.float32)
            faiss.normalize_L2(vec)
            self._faiss_index.add(vec)
            self._faiss_ids.append(skill_id)
            self._faiss_id_set.add(skill_id)

    def _faiss_search(
        self, query: list[float], k: int,
    ) -> list[tuple[str, float]]:
        """Return ``[(skill_id, cosine_similarity), ...]`` from faiss, top-k.

        Empty list when faiss not loaded or no data. The faiss HNSW index
        is built with default ``METRIC_L2`` over L2-normalized vectors;
        this means returned distances are squared L2 in [0, 4], which
        relates to cosine similarity by:

            ||a - b||² = 2 (1 - a·b)  →  cos = 1 - dist/2
        """
        # Hold _faiss_lock for the whole read: _faiss_note_insert mutates /
        # rebuilds the index + ids list concurrently during batch ingest, and
        # faiss HNSW is not safe for concurrent add+search (and the ids list
        # could grow mid-iteration). The lock also serializes the lazy build.
        with self._faiss_lock:
            if not self._faiss_load_or_build():
                return []
            q = np.asarray([query], dtype=np.float32)
            faiss.normalize_L2(q)
            k = min(k, self._faiss_index.ntotal)
            if k <= 0:
                return []
            dists, idx = self._faiss_index.search(q, k)
            out: list[tuple[str, float]] = []
            for n, i in enumerate(idx[0]):
                if 0 <= i < len(self._faiss_ids):
                    cos = 1.0 - float(dists[0][n]) / 2.0
                    out.append((self._faiss_ids[i], cos))
            return out

    # ------------------------------------------------------------------
    # dedup nearest-neighbor search (faiss) — only for ingest dedup, not runtime library search.
    # ------------------------------------------------------------------

    def find_near_duplicates(
        self, embedding: list[float],
        exclude_skill_id: str | None = None,
        top_k: int = 5,
        min_cosine: float = 0.92,
    ) -> list[tuple[SkillRecord, float]]:
        """Find embedding near-dup candidates (faiss HNSW + cosine).

        Returns [(SkillRecord, cosine), ...] sorted by cosine descending, filtered to
        >= min_cosine and excluding exclude_skill_id. Over-fetches 4× so soft-deleted/
        retired neighbors don't consume the top_k budget (faiss can't filter deleted=0
        itself; they are dropped below via a single batch query).
        """
        candidates = self._faiss_search(embedding, top_k * 4)
        if not candidates:
            return []
        cos_by_id = {
            sid: cos for sid, cos in candidates
            if sid != exclude_skill_id and cos >= min_cosine
        }
        recs = self._get_many_live(list(cos_by_id)) if cos_by_id else []
        out = [(rec, cos_by_id[rec.skill_id]) for rec in recs]
        out.sort(key=lambda x: -x[1])
        return out[:top_k]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_many_live(self, skill_ids: list[str]) -> list[SkillRecord]:
        """Batch-fetch non-deleted SkillRecords for ``skill_ids`` (order
        unspecified). Soft-deleted rows are dropped in one query instead of
        an N+1 ``get()`` loop. Chunked to stay under SQLite's variable limit.
        """
        if not skill_ids:
            return []
        conn = self._connect()
        out: list[SkillRecord] = []
        for i in range(0, len(skill_ids), 400):
            chunk = skill_ids[i:i + 400]
            ph = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT * FROM skills WHERE deleted = 0 AND skill_id IN ({ph})",
                chunk,
            ).fetchall()
            out.extend(self._row_to_record(r) for r in rows)
        return out

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> SkillRecord:
        cols = row.keys()
        superseded = row["superseded_by"] if "superseded_by" in cols else None
        return SkillRecord(
            skill_id=row["skill_id"],
            name=row["name"],
            description=row["description"],
            body=row["body"],
            frontmatter_raw=json.loads(row["frontmatter_raw"] or "{}"),
            source=row["source"],
            source_url=row["source_url"],
            source_path=row["source_path"],
            license=row["license"],
            content_hash=row["content_hash"],
            name_hash=row["name_hash"],
            category=row["category"],
            tags=json.loads(row["tags"] or "[]"),
            quality_score=float(row["quality_score"]),
            safety_flags=json.loads(row["safety_flags"] or "[]"),
            body_tokens=int(row["body_tokens"]),
            has_scripts=bool(row["has_scripts"]),
            has_references=bool(row["has_references"]),
            deleted=bool(row["deleted"]),
            superseded_by=superseded,
            added_at=row["added_at"],
            updated_at=row["updated_at"],
            stored_path=row["stored_path"],
        )
