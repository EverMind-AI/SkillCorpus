"""HubSkillSource — SkillForgeRouter source over the remote Skill Hub.

Discovery layer (Tier 0): turns ``GET /openapi/v1/skills?q=`` catalog
metadata into :class:`RouterHit` candidates for RRF fusion. The body
(``skill_md``) is NOT fetched here — that's the
the pre-gate body-hydrate step, which calls
``SkillHubClient.get(id)`` in parallel across all Hub candidates so the
LLM gate sees real body excerpts when deciding what to inject.

Bundled file download (zip → extract → resolved refs) happens in the
post-gate hydrate, only for the 0-2 hits the gate actually selects — so
catalog calls are O(K) but downloads are O(selected).

Failures are swallowed into an empty list by the router's
``_safe_search``, so a Hub outage never poisons the whole assembly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from skillsearch.relevance import check_keyword_relevance
from skillsearch.types import RouterHit

if TYPE_CHECKING:
    from skillsearch.hub_client import SkillHubClient

logger = logging.getLogger(__name__)


class HubSkillSource:
    """SkillSource backed by EverMind, guarded against forced unrelated Top K."""

    name: str = "hub"
    weight: float = 0.85

    def __init__(
        self,
        client: SkillHubClient,
        *,
        weight: float = 0.85,
        min_safety: float = 0.7,
        min_quality: float = 0.45,
        max_candidates: int = 2,
    ) -> None:
        self._client = client
        self.weight = weight
        self._min_safety = min_safety
        self._min_quality = min_quality
        self._max_candidates = max(0, max_candidates)

    async def search(self, query: str, history: list[dict[str, Any]], k: int) -> list[RouterHit]:
        del history
        limit = min(max(0, k), self._max_candidates)
        if limit == 0:
            return []
        items = await self._client.search(query, limit=max(limit * 4, limit))
        hits: list[RouterHit] = []
        for item in items:
            sid, name = item.get("id"), item.get("name")
            if not sid or not name:
                logger.warning("hub hit missing id/name; skipping: %r", item)
                continue
            safety, quality = item.get("score_safety"), item.get("quality_score")
            if safety is not None and float(safety) < self._min_safety:
                continue
            if quality is not None and float(quality) < self._min_quality:
                continue
            relevance = check_keyword_relevance(
                query, name=str(name), description=str(item.get("description") or ""), tags=item.get("tags")
            )
            if not relevance["passed"]:
                continue
            hits.append(
                RouterHit(
                    qualified_id=f"hub/{sid}",
                    name=str(name),
                    content="",
                    score=float(quality or 0.0),
                    meta={
                        "source": "hub",
                        "id": sid,
                        "skill_id": item.get("skill_id"),
                        "description": item.get("description"),
                        "tags": item.get("tags"),
                        "category": item.get("category"),
                        "quality_score": quality,
                        "install_count": item.get("install_count"),
                        "score_safety": safety,
                        "keyword_relevance": relevance,
                    },
                )
            )
            if len(hits) >= limit:
                break
        return hits


__all__ = ["HubSkillSource"]
