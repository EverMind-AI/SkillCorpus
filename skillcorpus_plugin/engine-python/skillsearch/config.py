"""One config shape, however the host spells it.

Raven keeps settings in a TOML plugin slice, Hermes in ``$HERMES_HOME``
JSON, OpenClaw in a ``configSchema`` of screaming-case env vars. Each
adapter translates its host's spelling into :class:`SearchConfig` and the
engine sees only this.

Two rules hold the abstraction together:

**Every field has a default that does something sensible.** ``SearchConfig()``
with no arguments is a working local-only setup. A host that knows nothing
about this package still gets retrieval by pointing at a skills directory.

**Absent capability is expressed by absent config, not by flags.** No
``hub_endpoint`` means no remote source; no ``gate_model`` means no gate.
There is no ``enable_hub`` to contradict an empty endpoint, so a config can
never say two things at once.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

_TRUE = {"1", "true", "yes", "on"}


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in _TRUE
    return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class LocalDir:
    """An extra directory of skills, beyond the main one."""

    path: str
    name: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class SearchConfig:
    """Everything the engine reads. Frozen — build a new one to change it."""

    # ── Sources ──────────────────────────────────────────────────────
    skills_dir: str = "skills"
    """Main skills directory. Relative paths resolve against ``workspace``."""

    extra_dirs: tuple[LocalDir, ...] = ()
    """Additional roots, e.g. a shared team library."""

    builtin_dir: str = ""
    """Read-only skills shipped with the host."""

    scan_depth: int = 5

    index_body: bool = False
    """Index skill bodies alongside name and description.

    Off by default: the description is what the ``SKILL.md`` format asks
    authors to write the trigger conditions into, and it is what the gate
    reads, so indexing it alone keeps ranking and gating looking at the
    same text. Turn this on for a corpus whose skills have thin
    descriptions — the cost of leaving it off is that a tool named only
    inside a body cannot be found by name."""

    hub_endpoint: str = ""
    """Remote catalog base URL. Empty disables the remote source."""

    hub_api_key: str = ""
    clawhub_endpoint: str = ""
    """ClawHub base URL. Empty disables this source."""
    skillhub_cn_endpoint: str = ""
    """skillhub.cn API base URL. Empty disables this source."""
    marketplace_timeout_s: float = 5.0
    marketplace_download_timeout_s: float = 30.0
    hub_timeout_s: float = 2.0
    """Deadline for a catalog query. Deliberately tight: a search runs on
    every turn, and a slow catalog must cost this turn its remote hits
    rather than the turn itself."""

    hub_download_timeout_s: float = 30.0
    """Deadline for fetching one bundle zip, which is megabytes rather
    than a JSON page. Separate from ``hub_timeout_s`` because the tight
    catalog deadline would fail every download of any real size."""
    hub_min_safety: float = 0.7

    # ── Fusion ───────────────────────────────────────────────────────
    weight_local: float = 1.0
    weight_hub: float = 0.85
    weight_clawhub: float = 0.75
    weight_skillhub_cn: float = 0.75

    rrf_k: int = 60
    """Rank-damping offset in the fusion. The paper's 60 by default.

    A host fusing a short head — a ``top_k`` of 3, say — may want it
    smaller: at 60 the weight gap between two sources exceeds every rank
    gap inside either, so fusion degrades into a seating order by source
    rather than by rank."""
    """Rank weights. Local ranks highest as hand-curated, remote lowest as
    unvetted. A source passed through ``extra_sources`` carries its own."""

    over_fetch: int = 2
    """Multiplier used before the per-source hard cap."""

    per_source_max: int = 2
    """Hard upper bound contributed by each source before fusion."""

    dedup_by: str = "name"
    top_k: int = 2

    # ── Narrowing ────────────────────────────────────────────────────
    model: str = ""
    """Model for the rewriter and gate. Empty runs retrieval raw."""

    rewrite: bool = True
    """Clean the query before searching. On by default: since it lost the
    power to veto retrieval it can only sharpen a match, never remove
    one."""

    rewrite_timeout_s: float = 5.0
    """Hard ceiling on the rewrite call. It runs before the gate on the
    same hot path, so it is bounded for the same reason — a stalled
    rewriter must degrade to "search the raw query", not hold the turn."""

    gate: bool | None = None
    """Let a model drop candidates before they reach the prompt.

    ``None`` — the default — means "on when a catalog is configured". The
    gate is a precision instrument, told to reject when unsure, and the two
    sources need opposite things from it:

    - **Local only.** These are the user's own skills, in a directory they
      curate. Ranking plus ``top_k`` is enough, and a "nothing here
      matches" answer now comes from retrieval itself, since an unrelated
      query returns empty once the corpus-adaptive stop words are pruned.
      A gate here mostly removes skills the user meant to have.
    - **With a catalog.** Tens of thousands of unvetted skills, where the
      best-ranked hit for an unrelated query is still *some* hit, and where
      the environment check — does this agent even have the tools this
      skill needs — is the only thing that catches a skill that cannot run
      here. That is worth a model call.

    An explicit ``True`` or ``False`` is always honoured.
    """

    gate_pool: int = 10
    max_select: int = 2
    gate_timeout_s: float = 20.0
    """Hard ceiling on the gate call. This runs on the turn's hot path, so
    it is bounded well below a model's own timeout — a slow gate must
    degrade to "inject the top hits" rather than stall the turn."""

    # ── Output ───────────────────────────────────────────────────────
    resolve_refs: bool = True
    """Rewrite ``{baseDir}`` and relative links in skill bodies to real
    paths. Requires the host and the skills to share a filesystem; turn it
    off when serving retrieval over HTTP."""

    heading: str = "# Skills"

    # ── Host-provided, not user-facing ───────────────────────────────
    workspace: str = "."
    cache_dir: str = ""
    """Where downloaded bundles are extracted. Defaults under ``workspace``."""

    def gate_enabled(self) -> bool:
        """Whether to build the gate, resolving the ``None`` default."""
        has_remote = bool(self.hub_endpoint or self.clawhub_endpoint or self.skillhub_cn_endpoint)
        return has_remote if self.gate is None else self.gate

    def resolved_skills_dir(self) -> Path | None:
        if not self.skills_dir:
            return None
        p = Path(os.path.expanduser(self.skills_dir))
        return p if p.is_absolute() else Path(self.workspace) / p

    def resolved_cache_dir(self) -> Path:
        """Where downloaded bundles are extracted.

        Deliberately a sibling of the skills directory rather than a child
        of it. Underneath, the local scanner would pick every downloaded
        bundle back up as a local skill, so a remote hit would reappear
        next turn as a second, local-looking copy of itself — competing
        with the original in the same ranking.
        """
        if self.cache_dir:
            return Path(os.path.expanduser(self.cache_dir))
        skills = self.resolved_skills_dir()
        base = skills.parent if skills else Path(self.workspace)
        return base / ".skillsearch-cache"

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> SearchConfig:
        """Build from a host's config dict, coercing strings as needed.

        Unknown keys are ignored rather than rejected: hosts hand over a
        whole config slice, and a key this version does not know is a
        forward-compatibility case, not an error.
        """
        raw = dict(data or {})
        kw: dict[str, Any] = {}
        for f in fields(cls):
            if f.name not in raw:
                continue
            v = raw[f.name]
            if f.name == "extra_dirs":
                kw[f.name] = tuple(LocalDir(**d) if isinstance(d, dict) else LocalDir(str(d)) for d in (v or []))
            # Coerce by the default's runtime type, not by ``f.type``:
            # ``from __future__ import annotations`` makes the latter the
            # string ``"bool"``, so every comparison against a type object
            # is quietly false.
            elif f.name == "gate":
                # Tri-state: the default is None ("decide from the sources"),
                # so it cannot be coerced by the default's type like the
                # rest. An explicit value, including a string from a host's
                # config file, becomes a real bool and is honoured.
                kw[f.name] = None if v is None else _as_bool(v, True)
            elif isinstance(f.default, bool):
                kw[f.name] = _as_bool(v, f.default)
            elif isinstance(f.default, int):
                kw[f.name] = _as_int(v, f.default)
            elif isinstance(f.default, float):
                kw[f.name] = _as_float(v, f.default)
            else:
                kw[f.name] = v if v is not None else f.default
        return cls(**kw)


__all__ = ["LocalDir", "SearchConfig"]
