"""The one thing a host calls.

``SkillSearch.retrieve(query)`` takes what the user asked and returns the
text to put in front of the model. Everything else in this package is an
implementation detail of that sentence.

The pipeline, in order:

1. **Rewrite** the message into a retrieval query, if a model is wired.
2. **Fan out** across configured sources and fuse by weighted RRF.
3. **Hydrate** bodies for hits that arrived as metadata only.
4. **Gate** the pool down with an LLM that drops what this agent cannot
   actually run here.
5. **Render**, resolving ``{baseDir}`` and relative links to real paths.

Steps 1, 4 and 5 are optional and degrade to no-ops. A host that wires
nothing but a skills directory still gets steps 2, 3 and a rendered block.

Failure is never raised at the caller. ``retrieve`` returns ``""`` on any
internal error, because it sits on the turn's hot path in every host that
uses it: a retrieval problem must cost the turn its skills, never the turn
itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import replace
from typing import Any

from skillsearch.config import SearchConfig
from skillsearch.ports import ChatModel, MemoryRecall, SkillStore
from skillsearch.types import RouterHit

log = logging.getLogger(__name__)


class SkillSearch:
    """Retrieval over local, remote and self-accumulated skills.

    Build once at host startup and keep it: sources hold an index and an
    HTTP connection pool, both of which want to outlive a single turn.
    """

    def __init__(
        self,
        config: SearchConfig,
        *,
        model: ChatModel | None = None,
        store: SkillStore | None = None,
        memory: MemoryRecall | None = None,
        hub_client: Any = None,
        get_tools: Any = None,
    ) -> None:
        """
        ``store`` — a host's own skill scanner. Omitted, one is built over
        ``config.skills_dir``.

        ``memory`` — recall over the agent's accumulated skills. Omitted,
        that source is simply absent.

        ``hub_client`` — a catalog client the host already built. Passing
        the one its own skill tools use keeps a single connection pool and
        a single catalog configuration; a client passed in is not closed
        by :meth:`aclose`, since the host still owns it. Omitted, one is
        built over ``config.hub_endpoint``.

        ``get_tools`` — a callable returning the tool names the agent has
        *this turn*. A callable rather than a list because in most hosts
        the tool set is not final when this object is built. The gate uses
        it to drop skills that need a tool the agent lacks; without it, the
        gate still runs, just without that check.
        """
        self._cfg = config
        self._model = model
        self._get_tools = get_tools
        self._store = store
        self._injected_hub = hub_client
        self._local_pool = None
        self._hub_client = None
        self._router = self._build_router(store, memory)
        self._rewriter, self._gate = self._build_narrowing()

    # ── Runtime control ──────────────────────────────────────────────

    @property
    def has_sources(self) -> bool:
        """Whether anything is configured to search.

        A host asks this to decide whether to install its retrieval hook
        at all. False when no skills directory exists, no catalog is
        configured and no memory backend was passed — in which case
        ``retrieve`` returns ``""`` for every query.
        """
        return self._router is not None

    def invalidate(self) -> None:
        """Drop the local scan and its index; the next search rescans.

        The scan is cached for the life of this object, so a ``SKILL.md``
        written after the first search is invisible until this is called.
        Hosts that watch their skills directory, or that write skills at
        runtime, call this from the same place they learn about the
        change. Costs one rescan on the next search and nothing now.
        """
        if self._store is not None and hasattr(self._store, "invalidate"):
            self._store.invalidate()
        if self._local_pool is not None:
            self._local_pool.rebuild_index()

    def set_provider(self, provider: ChatModel, model: str = "") -> None:
        """Adopt the model a live provider switch just built.

        The rewriter and the gate each hold the provider they were
        constructed with, so without this a host's ``/model`` switch
        moves the conversation to the new provider and leaves retrieval
        calling the old one — which is exactly wrong when the switch was
        made because the old credential stopped working.

        A no-op when retrieval was built without a model: there is
        nothing holding a provider to update.
        """
        self._model = provider
        for component in (self._rewriter, self._gate):
            if component is not None:
                component.set_provider(provider, model)

    # ── Assembly ─────────────────────────────────────────────────────

    def _build_router(self, store: SkillStore | None, memory: MemoryRecall | None):
        from skillsearch.router import SkillForgeRouter

        cfg = self._cfg
        sources: list[Any] = []

        local = self._build_local_source(store)
        if local is not None:
            sources.append(local)

        if self._injected_hub is not None or cfg.hub_endpoint:
            from skillsearch.sources.hub_source import HubSkillSource

            if self._injected_hub is not None:
                self._hub_client = self._injected_hub
            else:
                from skillsearch.hub_client import SkillHubClient

                self._hub_client = SkillHubClient(
                    cfg.hub_endpoint,
                    api_key=cfg.hub_api_key or None,
                    timeout_s=cfg.hub_timeout_s,
                    download_timeout_s=cfg.hub_download_timeout_s,
                    cache_dir=cfg.resolved_cache_dir(),
                )
            sources.append(
                HubSkillSource(
                    self._hub_client,
                    weight=cfg.weight_hub,
                    min_safety=cfg.hub_min_safety,
                ),
            )

        if memory is not None and cfg.agent_id:
            from skillsearch.sources.everos_source import EverosSkillSource

            sources.append(
                EverosSkillSource(
                    backend=memory,
                    agent_id=cfg.agent_id,
                    weight=cfg.weight_memory,
                )
            )

        if not sources:
            log.info("skillsearch: no sources configured; retrieval is off")
            return None
        log.info(
            "skillsearch: %d source(s): %s",
            len(sources),
            ", ".join(getattr(s, "name", "?") for s in sources),
        )
        return SkillForgeRouter(
            sources=sources,
            over_fetch_factor=cfg.over_fetch,
            dedup_by=cfg.dedup_by,
        )

    def _build_local_source(self, store: SkillStore | None):
        from skillsearch.local_pool import LocalPool
        from skillsearch.sources.local_source import LocalSkillSource

        cfg = self._cfg
        if store is None:
            root = cfg.resolved_skills_dir()
            roots: list[tuple[Any, str]] = []
            if root and root.is_dir():
                roots.append((root, "local"))
            from pathlib import Path

            if cfg.builtin_dir:
                builtin = Path(cfg.builtin_dir).expanduser()
                if builtin.is_dir():
                    roots.append((builtin, "builtin"))
            for extra in cfg.extra_dirs:
                if not extra.enabled:
                    continue
                p = Path(extra.path).expanduser()
                if p.is_dir():
                    roots.append((p, extra.name or p.name))
            if not roots:
                return None
            from skillsearch.local_store import DirectorySkillStore

            store = DirectorySkillStore(roots, max_depth=cfg.scan_depth)

        self._store = store
        self._local_pool = LocalPool(store)
        return LocalSkillSource(
            pool=self._local_pool,
            registry=store,
            weight=cfg.weight_local,
        )

    def _build_narrowing(self):
        cfg = self._cfg
        if self._model is None or not cfg.model:
            return None, None
        rewriter = gate = None
        if cfg.rewrite:
            from skillsearch.rewriter import QueryRewriter

            rewriter = QueryRewriter(self._model, model=cfg.model)
        if cfg.gate:
            from skillsearch.gate import LLMGateFilter

            gate = LLMGateFilter(
                self._model,
                max_select=cfg.max_select,
                legacy_top_k=cfg.max_select,
                model=cfg.model,
            )
        return rewriter, gate

    # ── The entry point ──────────────────────────────────────────────

    async def retrieve(
        self,
        query: str,
        *,
        history: list[dict[str, Any]] | None = None,
    ) -> str:
        """Return the block to inject, or ``""`` when there is nothing."""
        if self._router is None or not (query or "").strip():
            return ""
        try:
            hits = await self._search(query, history or [])
            if not hits:
                return ""
            return self._render(hits)
        except Exception as e:
            log.warning("skillsearch: retrieval failed (%s); injecting nothing", e)
            return ""

    async def hits(
        self,
        query: str,
        *,
        history: list[dict[str, Any]] | None = None,
    ) -> list[RouterHit]:
        """The selected skills as records, for hosts that render their own.

        Same pipeline as :meth:`retrieve` minus the final rendering — for a
        host whose prompt format differs enough that our block would look
        foreign in it.
        """
        try:
            return await self._search(query, history or [])
        except Exception as e:
            log.warning("skillsearch: retrieval failed (%s)", e)
            return []

    # ── Pipeline ─────────────────────────────────────────────────────

    async def _search(self, query: str, history: list[dict[str, Any]]) -> list[RouterHit]:
        cfg = self._cfg
        search_query = query
        if self._rewriter is not None:
            try:
                # Bounded here, not inside the rewriter: this is the first
                # model call on the turn's hot path, and it runs before the
                # gate, so an unbounded one stalls every turn.
                result = await asyncio.wait_for(
                    self._rewriter.analyze(query),
                    timeout=cfg.rewrite_timeout_s,
                )
                # The rewriter also judges whether the turn wants skills at
                # all — "thanks, that worked" does not. Honouring that skips
                # the fan-out entirely rather than ranking noise.
                if not getattr(result, "need_retrieval", True):
                    return []
                search_query = getattr(result, "rewritten_query", None) or query
            except TimeoutError:
                log.warning(
                    "skillsearch: rewrite exceeded %.0fs; searching the raw query",
                    cfg.rewrite_timeout_s,
                )
            except Exception as e:
                log.debug("skillsearch: rewrite failed (%s); using raw query", e)

        pool_size = cfg.gate_pool if self._gate is not None else cfg.top_k
        hits = await self._router.select(search_query, history, k=pool_size)
        if not hits:
            return []

        hits = await self._hydrate_bodies(hits)

        if self._gate is not None:
            hits = await self._run_gate(query, hits)
        hits = hits[: cfg.top_k]

        # Only the survivors: materialising a bundle is a download, so it
        # waits until the gate has decided what is actually going in.
        if cfg.resolve_refs:
            hits = await self._hydrate_refs(hits)
        return hits

    async def _run_gate(self, task: str, hits: list[RouterHit]) -> list[RouterHit]:
        """Gate with a hard deadline; on timeout keep the top hits.

        The gate is one LLM call on the turn's hot path. Left unbounded it
        would make retrieval the slowest thing in the turn, so a slow gate
        degrades to "inject what ranked highest" rather than holding up the
        response.
        """
        tools = None
        if self._get_tools is not None:
            try:
                raw = self._get_tools()
                tools = [_tool_name(t) for t in (raw or [])]
                tools = [t for t in tools if t]
            except Exception:
                tools = None
        try:
            return await asyncio.wait_for(
                self._gate.filter(task, hits, tools),
                timeout=self._cfg.gate_timeout_s,
            )
        except TimeoutError:
            log.warning(
                "skillsearch: gate exceeded %.0fs; keeping top %d unfiltered",
                self._cfg.gate_timeout_s,
                self._cfg.max_select,
            )
            return hits[: self._cfg.max_select]
        except Exception as e:
            log.warning("skillsearch: gate failed (%s); keeping top hits", e)
            return hits[: self._cfg.max_select]

    async def _hydrate_bodies(self, hits: list[RouterHit]) -> list[RouterHit]:
        """Fetch bodies for hits that arrived as catalog metadata only."""
        if self._hub_client is None:
            return hits
        targets = [(i, h) for i, h in enumerate(hits) if h.meta.get("source") == "hub" and not h.content]
        if not targets:
            return hits

        async def _one(hit: RouterHit) -> dict[str, Any] | None:
            try:
                return await self._hub_client.get(hit.meta["id"])
            except Exception as e:
                log.warning("skillsearch: body fetch failed for %s: %s", hit.meta.get("id"), e)
                return None

        metas = await asyncio.gather(*(_one(h) for _, h in targets))
        out = list(hits)
        for (i, hit), meta in zip(targets, metas, strict=True):
            if meta is None:
                continue
            new_meta = dict(hit.meta)
            new_meta["_fetched"] = meta
            out[i] = replace(hit, content=meta.get("skill_md", "") or "", meta=new_meta)
        return out

    async def _hydrate_refs(self, hits: list[RouterHit]) -> list[RouterHit]:
        """Put each selected skill's bundled files on disk and point at them.

        A skill body routinely says ``run scripts/x.py`` or ``see
        references/y.md``. Those paths mean nothing until the bundle is
        materialised, so this is what makes a skill usable rather than
        merely readable:

        - local — the directory is already known; resolve in place.
        - remote — download and extract the bundle, then resolve against
          the extracted directory. Cached by ``<slug>@<version>``, so a
          repeat is a stat.
        - anything else ships no files; passed through.

        An install failure keeps the unresolved body: the agent loses the
        absolute paths, not the instructions.
        """
        if not hits:
            return hits

        from skillsearch.refs import resolve_refs

        async def _one(hit: RouterHit) -> RouterHit:
            meta = hit.meta or {}
            source = meta.get("source")
            try:
                if source == "local":
                    skill_dir = meta.get("skill_dir")
                    if not skill_dir:
                        return hit
                    resolved, _ = resolve_refs(hit.content or "", skill_dir)
                    return replace(hit, content=resolved)
                if source == "hub" and self._hub_client is not None:
                    installed = await self._hub_client.install(
                        meta["id"],
                        prefetched_meta=meta.get("_fetched"),
                    )
                    body = installed.get("skill_md", "") or hit.content
                    resolved, _ = resolve_refs(body, installed.get("dir"))
                    new_meta = dict(meta)
                    new_meta["skill_dir"] = installed.get("dir")
                    return replace(hit, content=resolved, meta=new_meta)
            except Exception as e:
                log.warning(
                    "skillsearch: could not materialise %s (%s); injecting its body without resolved paths",
                    meta.get("id") or hit.name,
                    e,
                )
            return hit

        return list(await asyncio.gather(*(_one(h) for h in hits)))

    def render(self, hits: list[RouterHit]) -> str:
        """Render selected hits into the block. Public so an adapter can
        pair it with :meth:`hits` when the host also wants the ids."""
        return self._render(hits)

    def _render(self, hits: list[RouterHit]) -> str:
        """Render the block.

        When a skill's files are on disk, its header names the directory
        and says how to reach them — without that sentence an agent reads
        ``scripts/x.py`` as a path relative to its own cwd, which is the
        wrong place.
        """
        parts: list[str] = []
        for hit in hits:
            skill_dir = (hit.meta or {}).get("skill_dir")
            if skill_dir:
                header = (
                    f"### Skill: {hit.name}  [{hit.qualified_id}]\n"
                    f"**Skill directory**: `{skill_dir}`\n"
                    "Relative refs (e.g. `references/x.md`, `./scripts/y.sh`) "
                    "resolve under this directory — use the absolute form for "
                    "read_file / exec.\n"
                )
            else:
                header = f"### Skill: {hit.name}  [{hit.qualified_id}]\n"
            parts.append(header)
            content = (hit.content or "").strip()
            if content:
                parts.append(content)
        body = "\n\n".join(parts)
        return f"{self._cfg.heading}\n\n{body}" if body else ""

    async def aclose(self) -> None:
        """Release the HTTP pool this object built.

        A client the host injected is left open: the host is still using
        it elsewhere, and closing someone else's pool is not this object's
        call. Hosts with a shutdown hook should call this.
        """
        if self._injected_hub is None and self._hub_client is not None:
            with contextlib.suppress(Exception):
                await self._hub_client.aclose()


def _tool_name(tool: Any) -> str:
    """Accept the several shapes hosts describe a tool in."""
    if isinstance(tool, str):
        return tool
    if isinstance(tool, dict):
        fn = tool.get("function")
        if isinstance(fn, dict) and fn.get("name"):
            return str(fn["name"])
        if tool.get("name"):
            return str(tool["name"])
    return str(getattr(tool, "name", "") or "")


__all__ = ["SkillSearch"]
