"""Re-emit an agent's own recalled skills as router hits.

This is the source through which skills an agent accumulated for itself
join the fan-out. The backend's ``recall`` returns records; they are
re-wrapped as :class:`RouterHit` with the ``everos/`` prefix so the
router can fuse them with every other source.

The source is **host code, not part of any plugin**. The actual
backend behind it can be the bundled EverOS plugin or any other
:class:`MemoryRecall` adapter — for a backend that doesn't carry an
agent track (mem0 / MemOS / Letta), ``recall`` returns an empty list
and the source gracefully degrades.

Why we accept the ``agent_id`` in the constructor rather than at
search-time: it's a host-policy decision (driven by the agent's
config), not a per-query value. Putting it in ``__init__`` means each
``search`` stays cheap and there's exactly one place to audit who the
agent owner is.
"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING, Any

from skillsearch.types import RouterHit

if TYPE_CHECKING:
    from skillsearch.ports import MemoryRecall

logger = logging.getLogger(__name__)


def _stable_id_for(text: str) -> str:
    """Stable 12-hex-char fingerprint when the backend omits an id.

    EverMem returns proper ids; this is a safety net for adapters
    (mem0 / Letta) whose hit objects might not carry one. Same text
    always hashes to the same id, so feedback dispatch still has a
    consistent key even without an upstream id."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _short_name_for(text: str) -> str:
    """Display-name fallback: first non-blank line truncated to 40 chars."""
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[:40]
    return text[:40]


class EverosSkillSource:
    """Adapter turning a :class:`~skillsearch.ports.MemoryRecall`
    backend into a source the router can fan out to.

    ``weight = 0.9`` sits between the local directory (1.0 — hand-
    curated) and a remote catalog (0.85 — may not match this project's
    conventions). Self-accumulated skills are task-specific, so they
    outrank imported ones, but no human reviewed them, so they rank
    below what the team wrote.
    """

    name: str = "everos"
    weight: float = 0.9

    def __init__(
        self,
        backend: MemoryRecall,
        agent_id: str,
        *,
        weight: float | None = None,
    ) -> None:
        self._backend = backend
        self._agent_id = agent_id
        if weight is not None:
            self.weight = weight

    async def search(
        self,
        query: str,
        history: list[dict[str, Any]],
        k: int,
    ) -> list[RouterHit]:
        # ``history`` isn't forwarded today — the Protocol surface for
        # ``MemoryRecall.recall`` is intentionally small (query +
        # track id + top_k). When EverMem grows a "rerank by conversation
        # context" mode, we'll add an optional field here that
        # backends can ignore.
        del history

        hits = await self._backend.recall(
            query,
            agent_id=self._agent_id,
            top_k=k,
        )

        out: list[RouterHit] = []
        for m in hits:
            native_id = (m.metadata.get("id") if m.metadata else None) or _stable_id_for(m.text)
            name = (m.metadata.get("name") if m.metadata else None) or _short_name_for(m.text)
            out.append(
                RouterHit(
                    qualified_id=f"everos/{native_id}",
                    name=name,
                    content=m.text,
                    # Optional on the backend: fusion ranks by position,
                    # so a backend that reports no relevance number still
                    # ranks correctly. Carried only for a consumer that
                    # wants to show it.
                    score=float(getattr(m, "score", 0.0) or 0.0),
                    meta={
                        "source": "everos",
                        # The original Memory.metadata flows through —
                        # the after-turn feedback dispatcher reads
                        # things like ``owner_type`` / ``episode_type``
                        # / confidence when forming feedback signals.
                        **(m.metadata or {}),
                    },
                ),
            )
        return out


__all__ = ["EverosSkillSource"]
