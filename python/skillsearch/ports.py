"""What the engine needs from its host, stated as protocols.

Everything here is duck-typed on purpose. An adapter passes whatever its
host already has — Raven's ``LLMProvider``, Hermes's model client, a thin
wrapper over an HTTP endpoint — and as long as the shape matches, the
engine never learns which host it is running in.

Nothing in this package imports a host. That constraint is what makes the
core portable, and it is worth defending in review: an ``import raven`` or
``import hermes`` anywhere under ``skillsearch/`` is a bug, not a
shortcut.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ChatModel(Protocol):
    """A chat completion, used by the query rewriter and the relevance gate.

    Deliberately narrower than any host's provider interface: one method,
    messages in, text out. Adapters wrap their host's client to fit.

    Optional — leave it unset and the engine skips rewriting and gating
    rather than failing. Retrieval still runs.
    """

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 8192,
    ) -> str:
        """Return the assistant's reply text. Raise on transport failure."""
        ...


@runtime_checkable
class SkillStore(Protocol):
    """Read access to skills already on disk.

    Two methods, because that is all local retrieval needs: enumerate for
    the index, fetch one by name for the body. A host that already scans a
    skills directory (Raven's ``SkillRegistry``, Hermes's skill loader)
    can be passed straight through; hosts without one get
    :class:`skillsearch.local_store.DirectorySkillStore`.
    """

    def list_all(self) -> list[Any]:
        """Every known skill, as objects carrying ``name``, ``description``,
        ``content``, ``source`` and ``always``."""
        ...

    def get(self, name: str, source: str | None = None) -> Any | None:
        """One skill by name, or ``None``."""
        ...


@runtime_checkable
class MemoryRecall(Protocol):
    """Recall over an agent's own accumulated skills.

    The shape EverOS exposes, and the reason the everos source exists at
    all. Hosts without such a backend simply do not configure this source.
    """

    async def recall(self, *, agent_id: str, query: str, limit: int) -> list[Any]:
        """Skills this agent has accumulated, most relevant first."""
        ...


__all__ = ["ChatModel", "MemoryRecall", "SkillStore"]
