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
import hashlib
import logging
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from skillsearch.config import SearchConfig
from skillsearch.ports import ChatModel, SkillStore
from skillsearch.types import RouterHit

log = logging.getLogger(__name__)


class SkillSearch:
    """Retrieval over a host's own skills directory and a remote catalog.

    Build once at host startup and keep it: sources hold an index and an
    HTTP connection pool, both of which want to outlive a single turn.
    """

    def __init__(
        self,
        config: SearchConfig,
        *,
        model: ChatModel | None = None,
        store: SkillStore | None = None,
        hub_client: Any = None,
        get_tools: Any = None,
        extra_sources: Sequence[Any] = (),
    ) -> None:
        """
        ``store`` — a host's own skill scanner. Omitted, one is built over
        ``config.skills_dir``.

        ``extra_sources`` — sources this package does not know about,
        fused alongside the built-in two. Anything with a ``SkillSource``
        shape qualifies: a host's memory backend, a private library, a
        second catalog. The engine never learns which host it runs in, so
        a host wanting its own source writes the adapter and passes it
        here rather than teaching this package about it. Mirrors the
        TypeScript engine's ``EngineParts.sources``.

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
        self._marketplace_clients: dict[str, Any] = {}
        self._router = self._build_router(store, extra_sources)
        self._rewriter, self._gate = self._build_narrowing()

    # ── Runtime control ──────────────────────────────────────────────

    @property
    def has_sources(self) -> bool:
        """Whether anything is configured to search.

        A host asks this to decide whether to install its retrieval hook
        at all. False when no skills directory exists, no catalog is
        configured and no extra source was passed — in which case
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

    def _build_router(self, store: SkillStore | None, extra_sources: Sequence[Any]):
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
                    max_candidates=cfg.per_source_max,
                ),
            )

        marketplaces = (
            ("clawhub", cfg.clawhub_endpoint, cfg.weight_clawhub),
            ("skillhub_cn", cfg.skillhub_cn_endpoint, cfg.weight_skillhub_cn),
        )
        if any(endpoint for _, endpoint, _ in marketplaces):
            from skillsearch.sources.marketplace_source import MarketplaceClient, MarketplaceSkillSource

            for kind, endpoint, weight in marketplaces:
                if not endpoint:
                    continue
                marketplace = MarketplaceClient(
                    kind,
                    endpoint,
                    cache_dir=cfg.resolved_cache_dir(),
                    timeout_s=cfg.marketplace_timeout_s,
                    download_timeout_s=cfg.marketplace_download_timeout_s,
                )
                self._marketplace_clients[kind] = marketplace
                sources.append(MarketplaceSkillSource(marketplace, weight=weight))

        sources.extend(extra_sources)

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
            rrf_k=cfg.rrf_k,
            per_source_max=cfg.per_source_max,
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
        self._local_pool = LocalPool(store, index_body=cfg.index_body)
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
        if cfg.gate_enabled():
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
                # Only a cleaner query comes back. Deciding that a turn
                # wants no skills belongs to the gate, which sees the
                # shortlist and the agent's tools; the rewriter sees
                # neither and used to make that call anyway.
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
        hits = [hit for hit in hits if hit.meta.get("source") not in self._marketplace_clients or bool(hit.content)]
        hits = _dedup_exact_bodies(hits)
        if not hits:
            return []

        # Before the gate, and only for skills already on disk. The gate is
        # told to reject a skill whose files it cannot see, and an
        # unresolved `{baseDir}/scripts/x.py` reads exactly like one — so a
        # local skill that ships its own files was being rejected for
        # shipping them. Resolving first costs a few stats and no network.
        if cfg.resolve_refs:
            hits = self._resolve_local_refs(hits)

        if self._gate is not None:
            hits = await self._run_gate(query, hits)
        hits = hits[: cfg.top_k]

        # The remote half stays here: materialising a bundle is a download,
        # so it waits until the gate has decided what is actually going in.
        if cfg.resolve_refs:
            hits = await self._hydrate_refs(hits)
        hits = self._resolve_placeholders(hits)
        return hits

    def _resolve_local_refs(self, hits: list[RouterHit]) -> list[RouterHit]:
        """Resolve `{baseDir}` and relative links for hits already on disk.

        Synchronous on purpose: this is `os.stat` per referenced path, and
        it runs on the turn's hot path in front of the gate.
        """
        from skillsearch.refs import resolve_refs

        out: list[RouterHit] = []
        for hit in hits:
            meta = hit.meta or {}
            skill_dir = meta.get("skill_dir")
            if not skill_dir or not hit.content:
                out.append(hit)
                continue
            try:
                resolved, _ = resolve_refs(hit.content, skill_dir)
                out.append(replace(hit, content=resolved))
            except Exception as e:
                log.debug("skillsearch: could not resolve refs for %s (%s)", hit.name, e)
                out.append(hit)
        return out

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
        """Fetch bodies for all remote metadata hits before the gate."""
        targets = [
            (index, hit)
            for index, hit in enumerate(hits)
            if not hit.content
            and (hit.meta.get("source") == "hub" or hit.meta.get("source") in self._marketplace_clients)
        ]
        if not targets:
            return hits

        async def _one(hit: RouterHit) -> dict[str, Any] | None:
            try:
                source = hit.meta.get("source")
                if source == "hub" and self._hub_client is not None:
                    return await self._hub_client.get(hit.meta["id"])
                client = self._marketplace_clients.get(str(source))
                if client is not None:
                    installed = await client.install(hit)
                    return {"skill_md": installed["skill_md"], "_installed": installed}
            except Exception as error:
                log.warning("skillsearch: body fetch failed for %s: %s", hit.meta.get("id"), error)
            return None

        metas = await asyncio.gather(*(_one(hit) for _, hit in targets))
        output = list(hits)
        for (index, hit), meta in zip(targets, metas, strict=True):
            if meta is None:
                continue
            new_meta = dict(hit.meta)
            new_meta["_fetched"] = meta
            output[index] = replace(hit, content=meta.get("skill_md", "") or "", meta=new_meta)
        return output

    async def _hydrate_refs(self, hits: list[RouterHit]) -> list[RouterHit]:
        """Put each selected skill's bundled files on disk and point at them.

        A skill body routinely says ``run scripts/x.py`` or ``see
        references/y.md``. Those paths mean nothing until the bundle is
        materialised, so this is what makes a skill usable rather than
        merely readable:

        - local — already resolved before the gate, where the directory
          was known and the cost was a stat; passed through here.
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
                if source in self._marketplace_clients:
                    fetched = meta.get("_fetched", {})
                    installed = fetched.get("_installed") or await self._marketplace_clients[source].install(hit)
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

    def _resolve_placeholders(self, hits: list[RouterHit]) -> list[RouterHit]:
        """Fill PathGuard placeholders ({{SKILL_DIR}}, {{HOME}}, …) per agent.

        Unlike :meth:`_resolve_local_refs` / :meth:`_hydrate_refs` this never
        touches the filesystem and is not gated by ``resolve_refs``: a
        placeholder already names its target, and only the host knows it. It
        runs last, once every surviving hit has its ``skill_dir`` settled, so
        both local and remote bodies pass through the same pass.
        """
        from skillsearch.refs import resolve_placeholders

        cfg = self._cfg
        output_dir = cfg.output_dir or cfg.workspace
        out: list[RouterHit] = []
        for hit in hits:
            content = hit.content or ""
            if not content or "{{" not in content:
                out.append(hit)
                continue
            try:
                resolved = resolve_placeholders(
                    content,
                    (hit.meta or {}).get("skill_dir"),
                    state_dir=cfg.state_dir or None,
                    home_dir=cfg.home_dir or None,
                    output_dir=output_dir,
                )
            except Exception as e:
                log.debug("skillsearch: could not resolve placeholders for %s (%s)", hit.name, e)
                out.append(hit)
                continue
            out.append(replace(hit, content=resolved) if resolved != content else hit)
        return out

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
        for client in self._marketplace_clients.values():
            with contextlib.suppress(Exception):
                await client.aclose()


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


def _normalise_body(body: str) -> str:
    return "\n".join(line.rstrip() for line in body.replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip()


def _is_local_hit(hit: RouterHit) -> bool:
    return hit.meta.get("source") == "local" or bool(hit.meta.get("skill_dir"))


def _dedup_exact_bodies(hits: list[RouterHit]) -> list[RouterHit]:
    """Collapse exact instruction copies without fuzzy or model-based matching."""
    output: list[RouterHit] = []
    positions: dict[str, int] = {}
    for hit in hits:
        body = _normalise_body(hit.content)
        if not body:
            output.append(hit)
            continue
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        existing = positions.get(digest)
        if existing is None:
            positions[digest] = len(output)
            output.append(hit)
        elif _is_local_hit(hit) and not _is_local_hit(output[existing]):
            output[existing] = hit
    return output


__all__ = ["SkillSearch"]
