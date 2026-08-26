"""Core data types: :class:`RouterHit` and the :class:`SkillSource` protocol.

:class:`RouterHit` is self-contained — it carries the rendered ``content``,
not just a name and a score, so a caller can write it straight into a
prompt without going back to the source for the body.

:class:`SkillSource` is the seam for adding a source. Anything with a
``name``, a ``weight`` and ``async search(query, history, k)`` participates
in fusion; ``@runtime_checkable`` lets a test assert conformance without
inheritance. Three ship here (local, hub, memory-recall) and a host is
free to pass its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class RouterHit:
    """One ranked skill returned by a :class:`SkillSource`.

    Carries everything a consumer needs to render the
    skill into the system prompt — no further registry lookup happens
    on the consumer side.

    The ``qualified_id`` format is ``<source>/<native_id>``; the prefix
    is how the after-turn feedback dispatcher routes
    ``injected_skill_ids`` back to the right backend.
    """

    qualified_id: str
    """Globally-unique id with source prefix. Examples:
    ``"local/git-resolver"`` / ``"mass/curated-xyz"``. The slash split is
    unambiguous because source names are simple identifiers (no embedded
    slashes)."""

    name: str
    """Skill display name. Fusion may use it as a legacy configurable
    deduplication key, but the engine defaults to ``qualified_id`` and applies
    exact normalized-body deduplication after remote bodies are hydrated."""

    content: str
    """Pre-rendered SKILL.md body (frontmatter already stripped) that
    will land in the prompt's ``# Skills`` block. Empty string means
    the source has metadata but no body — consumers skip such hits
    from the body-join, while still letting the name appear in
    summaries."""

    score: float
    """Source-internal relevance. Each source normalizes to its own
    scale (BM25 raw / cosine sim / memory-recall score); the scales are
    not comparable, which is exactly why fusion never reads this value:
    ranking and collision representatives are both decided by weighted
    rank position. Kept as telemetry — the source's own justification
    for the order it returned."""

    meta: dict[str, Any] = field(default_factory=dict)
    """Source-specific escape hatch.

    Fusion stuffs ``rrf_score`` and ``contributing_sources`` here for
    telemetry; sources stuff their physical-origin label, native id,
    confidence, ``always`` flag, etc.
    """


@runtime_checkable
class SkillSource(Protocol):
    """One pool of skills the router can ask.

    The seam for adding a source: anything with a ``name``, a ``weight``
    and ``async search(query, history, k)`` participates in fusion, and
    the two that ship (local, hub) hold no privileged position. A host
    with a source of its own — a memory backend, a private library —
    writes it against this protocol and passes it to
    ``SkillSearch(extra_sources=...)``; this package never learns what it
    is.

    Why ``weight`` is a class attribute, not a method param: weights
    are router-wide policy, not per-call, so they belong with the
    source's identity. Tests and config tweaks set them once at
    construction.
    """

    name: str
    """Stable source identifier (``"local"`` / ``"mass"`` / a host's own).
    Used as the prefix in :attr:`RouterHit.qualified_id` and as the
    feedback-dispatch routing key."""

    weight: float
    """RRF source weight. Higher = source contributes more rank mass
    when the same skill surfaces from multiple sources."""

    async def search(
        self,
        query: str,
        history: list[dict[str, Any]],
        k: int,
    ) -> list[RouterHit]:
        """Return at most ``k`` :class:`RouterHit` records ranked best-first.

        ``history`` is the session-level message list — sources free to
        ignore it (Local does) or use it as context for a smarter ranker,
        which is why it is passed to every source rather than only to the
        ones that ship here.

        Empty list is a valid response — the router's
        ``_safe_search`` wrapper additionally turns exceptions into
        empty lists so a single source's failure doesn't poison the
        whole assembly.
        """
        ...


@dataclass
class SkillMeta:
    """One skill as a store hands it over.

    Structural, not nominal: a host's own skill object satisfies
    :class:`~skillsearch.ports.SkillStore` as long as it carries these
    attributes. This class is what
    :class:`~skillsearch.local_store.DirectorySkillStore` produces for
    hosts that have no scanner of their own.
    """

    name: str
    description: str
    content: str
    source: str = "local"
    path: Any = None
    always: bool = False


@dataclass
class ScoredSkill:
    """A local-index hit: which skill, and how well it matched."""

    name: str
    score: float
    source: str = ""


__all__ = ["RouterHit", "ScoredSkill", "SkillMeta", "SkillSource"]
