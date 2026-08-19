"""Fans one query out to every source and fuses the per-source rankings.

Two policies the router enforces, rather than leaving to its sources:

- **Per-source over-fetch.** :meth:`select(k)` asks every source for
  ``k * over_fetch_factor`` hits and lets RRF narrow to ``k`` overall,
  because a source's third hit can be a strong merge candidate even
  though it would never be a top-3 on its own. The factor defaults to 2.

- **Single-source failure isolation.** A source that raises is caught in
  :meth:`_safe_search` and contributes an empty list for that round, so
  one unreachable catalog costs its own results and not the retrieval.

The source list is fixed once the router is constructed, but not fixed by
this package: any :class:`~skillsearch.types.SkillSource` may be passed
in, and the three that ship hold no privileged position.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from skillsearch.fusion import RRF_K, rrf_merge_weighted
from skillsearch.types import RouterHit, SkillSource

logger = logging.getLogger(__name__)


class SkillForgeRouter:
    """Compose N :class:`SkillSource` outputs into one top-K ranking."""

    def __init__(
        self,
        sources: list[SkillSource],
        *,
        over_fetch_factor: int = 2,
        dedup_by: str = "name",
        rrf_k: int = RRF_K,
    ) -> None:
        # The list is captured by reference; callers should pass an
        # already-frozen tuple if they want to forbid mutation. We
        # deliberately don't freeze for them — host wires sources at
        # boot, never mutates, and verbose immutable wrappers add
        # nothing.
        self._sources = sources
        self._over_fetch_factor = max(1, over_fetch_factor)
        self._dedup_by = dedup_by
        self._rrf_k = rrf_k

    async def select(
        self,
        query: str,
        history: list[dict[str, Any]],
        k: int = 5,
    ) -> list[RouterHit]:
        """Fan out to every source concurrently, fuse to top-K."""
        per_source_k = k * self._over_fetch_factor
        per_source = await asyncio.gather(*[self._safe_search(s, query, history, per_source_k) for s in self._sources])
        return rrf_merge_weighted(
            [(s.name, s.weight, hits) for s, hits in zip(self._sources, per_source, strict=True)],
            k=k,
            dedup_by=self._dedup_by,
            rrf_k=self._rrf_k,
        )

    async def _safe_search(
        self,
        source: SkillSource,
        query: str,
        history: list[dict[str, Any]],
        k: int,
    ) -> list[RouterHit]:
        try:
            return await source.search(query, history, k)
        except Exception as e:
            # ``exception()`` writes the traceback; warning-level so a
            # transient blip doesn't spam ``error`` logs but still
            # shows up in normal aggregations.
            logger.warning(
                "skill source %r failed; treating as empty: %s",
                source.name,
                e,
            )
            return []


__all__ = ["SkillForgeRouter"]
