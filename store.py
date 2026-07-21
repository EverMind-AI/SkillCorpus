# =============================================================
# Data models (originally skill_library/models.py)
# =============================================================
"""数据模型 + SQLite schema.

SkillRecord 改造自 OpenSpace 的 SkillRecord:
  - 去掉 4 计数器 / lineage / evolution 字段 (不做执行追踪和进化)
  - 加 source / category / tags / quality_score / safety_flags 等库管理字段
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any


class Category(str, Enum):
    """主分类 LLM — 16 类(含 OTHER 兜底)。

    设计文档: ../docs/16_classification.md
    历史: 旧 15+1 类(CODING/BACKEND/AUTOMATION/...) 已 2026-05-20 替换。
    """
    # 软件开发栈 5 类
    DEV = "DEV"
    FRONTEND_UI = "FRONTEND-UI"
    DEVOPS_INFRA = "DEVOPS-INFRA"
    TESTING = "TESTING"
    SECURITY = "SECURITY"

    # 数据 / AI 2 类
    DATA = "DATA"
    AI_ML = "AI-ML"

    # 认证 1 类
    AUTH = "AUTH"

    # 内容输出 4 类
    DOC_PROC = "DOC-PROC"
    WRITING = "WRITING"
    MULTIMEDIA = "MULTIMEDIA"
    COMMS = "COMMS"

    # 流程 / 办公 2 类
    WORKFLOW = "WORKFLOW"
    PRODUCTIVITY = "PRODUCTIVITY"

    # 元工具 1 类
    META = "META"

    # 兜底
    OTHER = "OTHER"


CATEGORIES: list[str] = [c.value for c in Category]


CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "DEV":          "通用编码 / SaaS API 集成 / SDK wrapper / 通用后端逻辑",
    "FRONTEND-UI":  "前端 / 移动 / UI 组件 / 设计系统 / 可视布局",
    "DEVOPS-INFRA": "CI/CD / 部署 / 容器 / k8s / 云基础设施 / 监控",
    "DATA":         "结构化/量化输出 — 数据工程 / ETL / 数据库 / SQL / BI / 分析 / 参考库",
    "AI-ML":        "产物即 AI 系统 — agent / persona / 多 agent / RAG / 训练推理",
    "TESTING":      "软件/硬件测试 — 单测 / 集成 / E2E / fuzz / QA / debug / 测试规划",
    "SECURITY":     "漏洞扫描 / pen-test / 加密 / 威胁检测 / 审计 / forensics",
    "AUTH":         "认证 / 授权 / OAuth / SSO / IAM / token / 权限管理",
    "DOC-PROC":     "处理已有文档 — pdf/docx/xlsx/pptx/md 解析提取转换",
    "WRITING":      "生成原创 prose — 文章/邮件/报告/战略文档/咨询/总结",
    "MULTIMEDIA":   "图 / 视频 / 音频 生成或处理",
    "COMMS":        "消息渠道集成 — 邮件/IM/Slack/Teams/Discord/钉钉/微信",
    "WORKFLOW":     "多步业务流程 / playbook / 跨步骤编排(非 AI agent / 非 CI/CD)",
    "PRODUCTIVITY": "单点办公 — 日程/预约/admin/笔记/单步记录",
    "META":         "skill 创建/管理工具 — skill builder / 注册中心 / MCP server",
    "OTHER":        "纯 lifestyle / 学术 / 工程长尾 — 无适配活动",
}


@dataclass
class SkillRecord:
    """Skill 记录 — 库的核心数据模型."""

    # --- 身份 ---
    skill_id: str                       # {source}__{name_slug}__{hash8}
    name: str                           # frontmatter 里的 name
    description: str                    # frontmatter 里的 description
    body: str                           # SKILL.md 正文 (不含 frontmatter)
    frontmatter_raw: dict[str, Any] = field(default_factory=dict)

    # --- 来源 ---
    source: str = ""                    # "anthropics" | "karanb192" | "clawhub" | ...
    source_url: str | None = None
    source_path: str = ""               # 原仓库内相对路径
    license: str | None = None

    # --- 内容 hash (去重用) ---
    content_hash: str = ""              # SHA-256 (normalized body)
    name_hash: str = ""                 # SHA-256 (lowercased name)

    # --- 分类 ---
    category: str = Category.OTHER.value
    tags: list[str] = field(default_factory=list)

    # --- 质量 ---
    quality_score: float = 0.0          # 0.0 - 1.0
    safety_flags: list[str] = field(default_factory=list)
    body_tokens: int = 0                # 粗略 tiktoken 估算

    # --- 结构特征 ---
    has_scripts: bool = False
    has_references: bool = False

    # --- 状态 ---
    deleted: bool = False               # soft delete 标记
    # 跨 source 近似去重后被合并的 skill 会记录 winner 的 skill_id
    # (同时 deleted=True). 这样 winner 对应的文件/元数据仍可追溯.
    superseded_by: str | None = None

    # --- 时间 ---
    added_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # --- 可选存储路径 ---
    stored_path: str = ""               # 库内存储路径 (相对 library root)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SkillRecord":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS skills (
    skill_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    body TEXT NOT NULL,
    frontmatter_raw TEXT NOT NULL DEFAULT '{}',       -- JSON
    source TEXT NOT NULL,
    source_url TEXT,
    source_path TEXT NOT NULL DEFAULT '',
    license TEXT,
    content_hash TEXT NOT NULL,
    name_hash TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'OTHER',
    tags TEXT NOT NULL DEFAULT '[]',                   -- JSON array
    quality_score REAL NOT NULL DEFAULT 0.0,
    safety_flags TEXT NOT NULL DEFAULT '[]',           -- JSON array
    body_tokens INTEGER NOT NULL DEFAULT 0,
    has_scripts INTEGER NOT NULL DEFAULT 0,            -- 0/1
    has_references INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    superseded_by TEXT,                                -- 被近似去重合并时指向 winner skill_id
    added_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    stored_path TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 0                   -- GREEN-license gate; 0=excluded from export (safe-by-default)
);

CREATE INDEX IF NOT EXISTS idx_skills_source      ON skills(source);
CREATE INDEX IF NOT EXISTS idx_skills_category    ON skills(category);
CREATE INDEX IF NOT EXISTS idx_skills_content_hash ON skills(content_hash);
CREATE INDEX IF NOT EXISTS idx_skills_name_hash    ON skills(name_hash);
CREATE INDEX IF NOT EXISTS idx_skills_name         ON skills(name);
CREATE INDEX IF NOT EXISTS idx_skills_deleted      ON skills(deleted);
-- superseded_by 索引由 store._migrate() 在 ALTER TABLE 之后创建
"""

# Vector table (sqlite-vec). Created separately in store.py since it
# requires loading the sqlite-vec extension.
VEC_TABLE_NAME = "vec_skills"

# =============================================================
# Storage layer (originally skill_library/storage.py, now merged here)
# =============================================================
#
# Storage layer: SQLite (元数据) + sqlite-vec (向量, source of truth) + filesystem (源文件).
#
# 改造自 OpenSpace openspace/skill_engine/store.py:
#   - 去掉 evolution_suggestions / execution_analyses / tool_quality 表
#   - 保留 skills 表 + 向量索引
#   - 同步 (sync) 接口, 非 async
#
# 向量检索路径 (2026-04-29 优化):
#   - 写入仍走 sqlite-vec (vec_skills 表是 source of truth, 不丢)
#   - 读取 (vector_search / find_near_duplicates) 优先走 faiss HNSW 索引
#     (实测 80K 行 cosine top-5: sqlite-vec 800ms → faiss 0.4ms, ~2000x)
#   - faiss 索引文件 ``<db_dir>/skill_index.faiss`` + ``skill_index_ids.json``
#     缺失时自动从 vec_skills rebuild; 长期累积漂移可手动 rebuild_faiss_index()

import json
import logging
import shutil
import sqlite3
import struct
import threading
from pathlib import Path
from typing import Any

import sqlite_vec


# faiss 是硬依赖 (dedup 近邻检索走它, 无 "没装 faiss" 的软降级路径)。
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

logger = logging.getLogger("skill_library.store")


class SkillStore:
    """SQLite + sqlite-vec 持久化."""

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
        self._safe_sources: frozenset[str] | None = None
        self._safe_sources_mtime: float | None = None   # JSON mtime when cached

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
        """创建所有表 (幂等) + 运行必要的小型迁移."""
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
        self._migrate()

    def _migrate(self) -> None:
        """幂等迁移 — 给老 DB 加新列 / 重算 hash. 用 user_version 跟踪状态."""
        conn = self._connect()
        existing_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(skills)").fetchall()
        }
        # Round A (1): 加 superseded_by 列
        if "superseded_by" not in existing_cols:
            conn.execute("ALTER TABLE skills ADD COLUMN superseded_by TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_skills_superseded_by ON skills(superseded_by)"
            )
            logger.info("migrated: added skills.superseded_by column")
        # License-gate (2): 加 active 列 (old DBs created before the GREEN
        # filter existed). New DBs get it from SCHEMA_SQL; this is the
        # backfill path for pre-existing index.db without the column.
        if "active" not in existing_cols:
            conn.execute("ALTER TABLE skills ADD COLUMN active INTEGER NOT NULL DEFAULT 0")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_skills_active ON skills(active)"
            )
            logger.info("migrated: added skills.active column")
        conn.commit()

        # Round A (2): 重算所有 name_hash 为 canonical 形式, 一次性
        cur_v = conn.execute("PRAGMA user_version").fetchone()[0]
        if cur_v < 1:
            from .dedup import name_hash as _canonical_name_hash
            rows = conn.execute("SELECT skill_id, name FROM skills").fetchall()
            updated = 0
            for r in rows:
                conn.execute(
                    "UPDATE skills SET name_hash = ? WHERE skill_id = ?",
                    (_canonical_name_hash(r["name"]), r["skill_id"]),
                )
                updated += 1
            conn.execute("PRAGMA user_version = 1")
            conn.commit()
            if updated:
                logger.info(f"migrated: recomputed canonical name_hash for {updated} skills")

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

    def _load_safe_sources(self) -> frozenset[str]:
        """Load the GREEN-license source whitelist, cached + mtime-invalidated.

        Source: ``license_safe_sources.json`` at the ``skill_library``
        package root (next to ``config.yaml``). Missing file means the
        license filter is uninitialized; we return an empty set, which
        makes every new insert default to ``active=0`` (safe-by-default
        — operator can flip individual rows via SQL before next export).

        The cache is keyed on the file's mtime, so regenerating the JSON
        (``license_audit build``) mid-process is picked up by subsequent
        inserts instead of silently using the stale whitelist.
        """
        # license_safe_sources.json lives at the package root (sibling
        # of config.yaml / sources.yaml), not under data/. Resolve from
        # this module's location rather than the DB path.
        json_path = Path(__file__).resolve().parent / 'license_safe_sources.json'
        try:
            mtime = json_path.stat().st_mtime
        except FileNotFoundError:
            mtime = None
        if self._safe_sources is not None and mtime == self._safe_sources_mtime:
            return self._safe_sources

        if mtime is None:
            logger.warning(
                "license_safe_sources.json missing at %s — "
                "new inserts will default to active=0 (excluded from export).",
                json_path,
            )
            sources = frozenset()
        else:
            with open(json_path) as f:
                data = json.load(f)
            sources = frozenset(data.get('sources', []))
            logger.info(
                "loaded license whitelist: %d GREEN sources from %s",
                len(sources), json_path,
            )
        self._safe_sources = sources
        self._safe_sources_mtime = mtime
        return sources

    def insert(self, rec: SkillRecord, embedding: list[float] | None = None) -> None:
        # Store-side gate is intentionally **source-level only** (GREEN source
        # whitelist), the conservative default. The richer per-skill rule lives
        # in license_filter.is_green_license / license_audit, which run at a
        # layer that can see the source-license CSV — keep them as the single
        # place that may widen active, so this hot path stays cheap.
        active = 1 if rec.source in self._load_safe_sources() else 0
        # Anti-clobber: INSERT OR REPLACE (and update(), which round-trips
        # through here) would otherwise reset active=0 on every re-ingest,
        # silently undoing an operator's manual SQL activation (the escape
        # hatch documented in _load_safe_sources). Only ever upgrade, never
        # downgrade, an existing active=1 row.
        if active == 0:
            prev = self._connect().execute(
                "SELECT active FROM skills WHERE skill_id = ?", (rec.skill_id,)
            ).fetchone()
            if prev is not None and prev["active"] == 1:
                active = 1

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
        """把 loser_id 标记为被 winner_id 合并取代 (soft delete + superseded_by)."""
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
    # 去重近邻检索 (faiss) — 仅用于入库 dedup, 不是 runtime 搜库。
    # ------------------------------------------------------------------

    def find_near_duplicates(
        self, embedding: list[float],
        exclude_skill_id: str | None = None,
        top_k: int = 5,
        min_cosine: float = 0.92,
    ) -> list[tuple[SkillRecord, float]]:
        """查 embedding 近似重复候选 (faiss HNSW + cosine)。

        返回 [(SkillRecord, cosine), ...] 按 cosine 降序, 过滤 >= min_cosine
        且排除 exclude_skill_id。over-fetch 4× 让软删/废弃邻居不占 top_k 额度
        (faiss 不能自己过滤 deleted=0; 下面用一次批量查丢掉)。
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


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


def copy_skill_to_library(
    src_dir: Path, lib_root: Path, source: str, name_slug: str,
    meta: dict | None = None,
) -> Path:
    """把 skill 目录从 src_dir 拷到 lib_root/skills/<source>/<name_slug>/.

    保留 SKILL.md + scripts/ + references/ + 所有其他文件.
    追加 .meta.json 记录 ingest 元数据.
    """
    dst_dir = lib_root / "skills" / source / name_slug
    if dst_dir.exists():
        shutil.rmtree(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)

    for item in src_dir.iterdir():
        if item.name.startswith("."):
            continue
        if item.is_file():
            shutil.copy2(item, dst_dir / item.name)
        elif item.is_dir():
            shutil.copytree(item, dst_dir / item.name)

    if meta:
        (dst_dir / ".meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return dst_dir


def remove_skill_from_library(lib_root: Path, stored_path: str) -> bool:
    full = lib_root / stored_path
    if full.exists() and full.is_dir():
        shutil.rmtree(full)
        return True
    return False
