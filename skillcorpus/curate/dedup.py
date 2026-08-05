"""curate.dedup — LLM-assisted near-duplicate adjudication (Round A).

The pure hashes / cosine moved to core.hashing; this module keeps the
SQLite-cached LLMDupJudge that the ingest pipeline calls on embedding-near
candidate pairs.
"""
from __future__ import annotations

# =============================================================
# LLM-assisted near-duplicate adjudication (originally dedup_llm.py)
# =============================================================
#
# LLM pairwise duplicate adjudication — a second-confirmation gate for near-match embedding candidates.
#
# Flow:
#     candidate pairs with embedding cos >= near_dup_min_cosine
#         → LLMDupJudge.is_duplicate(a, b)
#         → returns (is_dup: bool, confidence: float, reason: str)
#
# Results are cached in the SQLite table `dedup_judgments` (key = sorted content_hash pair),
# to avoid calling the LLM repeatedly for the same skill pair.
#
# The LLM is only called on a cache miss; a cache hit returns directly.

import logging
import sqlite3
from dataclasses import dataclass

from ..core.llm import LLMClient
from ..core.models import SkillRecord

logger = logging.getLogger("skillcorpus.dedup")


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
    """Build the dedup prompt — provide both skills in full and let the LLM decide whether they are substantially synonymous."""
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
    """LLM pairwise dedup judging — with a SQLite cache."""

    def __init__(self, llm: LLMClient, conn: sqlite3.Connection):
        self.llm = llm
        self.conn = conn
        # A SQLite Connection is not truly thread-safe: even with check_same_thread=False,
        # concurrent execute() from multiple threads hits ``InterfaceError``. Use a Lock to
        # serialize SQLite while keeping the LLM HTTP calls free to run concurrently — HTTP is
        # the real bottleneck, and the lock is only held for milliseconds.
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
        """Decide whether two skills are duplicates. Return directly on cache hit; otherwise call the LLM and write to cache."""
        key = _pair_key(a.content_hash, b.content_hash)
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        msgs = _build_prompt(a, b)
        raw = self.llm.chat(msgs, response_format="json", max_tokens=256)
        d = LLMClient.extract_json(raw)
        if not isinstance(d, dict):
            # LLM failed → conservatively judge as non-duplicate (don't write cache, retry next time)
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
