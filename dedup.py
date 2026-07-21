"""去重: SHA-256 精确 (body) + canonical name + embedding 近似.

三种去重层次:
  content_hash(body)   精确 — 完全相同的 body 复制上传
  name_hash(name)      近似 — canonical_name 归一, 大小写/标点/分隔符差异忽略
  embedding cosine     语义 — 名字完全不同但内容接近
"""

from __future__ import annotations

import hashlib
import re

_WS_RE = re.compile(r"\s+")
# canonical_name 里把 . _ - / 等常见分隔符全替成空格再压缩
_NAME_SEP_RE = re.compile(r"[^a-z0-9]+")


def normalize_body(body: str) -> str:
    """归一化 body 文本 (去首尾空白 + 压缩连续空白) 用于 hash 比较."""
    text = body.strip()
    # 压缩连续空白但保留单个换行语义
    lines = [_WS_RE.sub(" ", line.strip()) for line in text.splitlines()]
    return "\n".join(l for l in lines if l)


def content_hash(body: str) -> str:
    """计算归一化 body 的 SHA-256 (去除空白差异)."""
    normalized = normalize_body(body)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def canonical_name(name: str) -> str:
    """把 skill name 归一为 canonical 形式, 吸收大小写/标点/分隔符差异.

    示例:
      "react-hooks"   → "react hooks"
      "React Hooks"   → "react hooks"
      "react_hooks"   → "react hooks"
      "React.Hooks!!" → "react hooks"
      "  pdf   gen "  → "pdf gen"
    """
    s = name.strip().lower()
    s = _NAME_SEP_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def name_hash(name: str) -> str:
    """计算 canonical_name 的 SHA-256.

    用 canonical 形式后, "react-hooks" / "React Hooks" / "react_hooks"
    命中同一 hash, 跨 source 的近似同名也能被识别为冲突.
    """
    return hashlib.sha256(canonical_name(name).encode("utf-8")).hexdigest()


def short_hash(full_hash: str, n: int = 8) -> str:
    """取 hash 前 n 位作为 skill_id 后缀."""
    return full_hash[:n]


# ---------------------------------------------------------------------------
# 近似相似度 (用在 embedding 检索之后)
# ---------------------------------------------------------------------------

def cosine_sim(a: list[float], b: list[float]) -> float:
    """纯 Python 计算 cosine 相似度. 小规模向量够用."""
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)

# =============================================================
# LLM-assisted near-duplicate adjudication (originally dedup_llm.py)
# =============================================================
#
# LLM 成对重复判决 — 近似 embedding 候选的二次确认闸门.
#
# 流程:
#     embedding cos >= near_dup_min_cosine 的候选对
#         → LLMDupJudge.is_duplicate(a, b)
#         → 返回 (is_dup: bool, confidence: float, reason: str)
#
# 结果缓存到 SQLite 表 `dedup_judgments` (key = sorted content_hash 对),
# 避免同一对 skill 重复调用 LLM.
#
# cache miss 才调 LLM; cache hit 直接返回.

import logging
import sqlite3
from dataclasses import dataclass

from .llm import LLMClient
from .store import SkillRecord

logger = logging.getLogger("skill_library.dedup")


DEDUP_JUDGMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS dedup_judgments (
    pair_key TEXT PRIMARY KEY,     -- sorted(content_hash_a, content_hash_b) joined by ':'
    is_duplicate INTEGER NOT NULL,
    confidence REAL NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    judged_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dedup_judgments_is_dup ON dedup_judgments(is_duplicate);
"""


@dataclass
class DupJudgment:
    is_duplicate: bool
    confidence: float
    reason: str
    cached: bool = False


def _pair_key(hash_a: str, hash_b: str) -> str:
    a, b = sorted([hash_a, hash_b])
    return f"{a}:{b}"


def _build_prompt(a: SkillRecord, b: SkillRecord) -> list[dict[str, str]]:
    """构造判重 prompt — 两 skill 给全, LLM 判是否实质同义."""
    def _slice(s: str, n: int) -> str:
        return s[:n].rstrip() + (" ..." if len(s) > n else "")

    user_content = (
        "You are deciding whether two agent skills are DUPLICATES.\n"
        "Two skills are duplicates when they solve essentially the SAME task "
        "with the SAME approach — minor wording/formatting differences are fine.\n"
        "Two skills are NOT duplicates if they:\n"
        "  - use different tools/libraries for the same problem\n"
        "  - address different sub-tasks within the same domain\n"
        "  - have substantially different depth or scope\n\n"
        "Output STRICT JSON only, no prose, no markdown fences.\n"
        "Required keys: is_duplicate (bool), confidence (0.0-1.0), reason (short string).\n\n"
        "=== Skill A ===\n"
        f"Name: {a.name}\n"
        f"Source: {a.source}\n"
        f"Description: {_slice(a.description, 400)}\n"
        f"Body: {_slice(a.body, 1200)}\n\n"
        "=== Skill B ===\n"
        f"Name: {b.name}\n"
        f"Source: {b.source}\n"
        f"Description: {_slice(b.description, 400)}\n"
        f"Body: {_slice(b.body, 1200)}\n\n"
        "Output JSON:"
    )
    return [
        {"role": "system",
         "content": "You are an expert at detecting duplicate documentation. Output only valid JSON."},
        {"role": "user", "content": user_content},
    ]


class LLMDupJudge:
    """LLM 成对判重 — 带 SQLite cache."""

    def __init__(self, llm: LLMClient, conn: sqlite3.Connection):
        self.llm = llm
        self.conn = conn
        # SQLite Connection 不是真正线程安全, 即便 check_same_thread=False,
        # 多线程并发 execute 会撞 ``InterfaceError``. 用 Lock 串行化 SQLite,
        # 保留 LLM HTTP 调用的并发自由 — HTTP 才是真瓶颈, lock 只 hold ms 级.
        import threading
        self._sqlite_lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._sqlite_lock:
            self.conn.executescript(DEDUP_JUDGMENT_SCHEMA)
            self.conn.commit()

    def _cache_get(self, key: str) -> DupJudgment | None:
        with self._sqlite_lock:
            row = self.conn.execute(
                "SELECT is_duplicate, confidence, reason FROM dedup_judgments WHERE pair_key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return DupJudgment(
            is_duplicate=bool(row[0]),
            confidence=float(row[1]),
            reason=row[2] or "",
            cached=True,
        )

    def _cache_put(self, key: str, j: DupJudgment) -> None:
        from datetime import datetime, timezone
        with self._sqlite_lock:
            self.conn.execute(
                """INSERT OR REPLACE INTO dedup_judgments
                   (pair_key, is_duplicate, confidence, reason, judged_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (key, int(j.is_duplicate), j.confidence, j.reason,
                 datetime.now(timezone.utc).isoformat()),
            )
            self.conn.commit()

    def is_duplicate(self, a: SkillRecord, b: SkillRecord) -> DupJudgment:
        """判两个 skill 是否重复. 缓存命中直接返回, 否则调 LLM 并写 cache."""
        key = _pair_key(a.content_hash, b.content_hash)
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        msgs = _build_prompt(a, b)
        raw = self.llm.chat(msgs, response_format="json", max_tokens=256)
        d = LLMClient.extract_json(raw)
        if not isinstance(d, dict):
            # LLM 失败 → 保守判非重复 (不写 cache, 下次再试)
            logger.warning(
                f"LLM dedup judge failed for {a.skill_id} vs {b.skill_id}: raw={raw!r}"
            )
            return DupJudgment(is_duplicate=False, confidence=0.0,
                               reason="llm_failed", cached=False)

        try:
            is_dup = bool(d.get("is_duplicate", False))
            conf = float(d.get("confidence", 0.5))
            conf = max(0.0, min(1.0, conf))
            reason = str(d.get("reason", ""))[:500]
        except Exception as e:
            logger.warning(f"LLM dedup judge malformed output {d!r}: {e}")
            return DupJudgment(is_duplicate=False, confidence=0.0,
                               reason="llm_malformed", cached=False)

        j = DupJudgment(is_duplicate=is_dup, confidence=conf, reason=reason)
        self._cache_put(key, j)
        return j

    def stats(self) -> dict[str, int]:
        row = self.conn.execute(
            "SELECT COUNT(*) AS total, SUM(is_duplicate) AS n_dup FROM dedup_judgments"
        ).fetchone()
        total = int(row[0] or 0)
        n_dup = int(row[1] or 0)
        return {"total_judgments": total, "confirmed_duplicates": n_dup}
