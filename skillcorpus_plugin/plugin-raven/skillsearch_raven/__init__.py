"""Raven adapter — a context segment.

Raven assembles its prompt from an ordered list of named stages and lets a
plugin claim one by name. Retrieval claims ``skills``.

Ship alongside a ``raven-plugin.toml``::

    [plugin]
    id                 = "skillsearch"
    version            = "0.2.0"
    bundled            = false
    enabled_by_default = true

    [[plugin.contributes.context_segments]]
    name     = "skills"
    factory  = "skillsearch_raven:make_segment"
    replaces = "skills"

Raven hands the factory a ``PluginContext`` carrying the plugin's config
slice and a narrow set of host services. Only one service is used: the
callable that answers which tools the agent has this turn.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from skillsearch.config import SearchConfig
from skillsearch.engine import SkillSearch

log = logging.getLogger(__name__)


class _ProviderAdapter:
    """Raven's ``LLMProvider`` seen through the ``ChatModel`` protocol."""

    def __init__(self, provider: Any) -> None:
        self._p = provider

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 8192,
    ) -> str:
        resp = await self._p.chat_with_retry(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return _text_of(resp)


class SkillsSegment:
    """A Raven ``SegmentBuilder`` over :class:`SkillSearch`.

    The three class attributes are the host's ``SegmentBuilder`` protocol,
    read by :class:`ContextAssembler` before anything is built: ``order``
    sorts the builders into the system prompt, and ``needs_prefix`` routes
    a builder to the phase that can read the assembled prefix. Retrieval
    reads only the current message, so it stays in the parallel phase.

    ``order = 5`` is the slot the host reserves for ``# Skills``, between
    the always-on skills (4) and the Curator (6). Missing these attributes
    does not fail at build time — the assembler raises while sorting, so
    the agent never starts.
    """

    name = "skills"
    order = 5
    needs_prefix = False

    def __init__(self, search: SkillSearch) -> None:
        # No heading parameter: the heading is rendered by the engine from
        # ``SearchConfig.heading``, so one here could only disagree with
        # the text it claims to head.
        self._search = search

    async def build(self, ctx: Any) -> Any:
        from raven.context_engine.base import Segment  # host type, adapter-local

        query = getattr(ctx, "current_message", "") or ""
        history = list(getattr(ctx, "session_messages", None) or [])
        try:
            hits = await self._search.hits(query, history=history)
            text = self._search.render(hits) if hits else ""
        except Exception as e:
            # Declining the stage is the only safe failure here. The host
            # keeps no built-in fallback for `skills`, and an exception out
            # of a segment builder aborts assembly — so a broken catalog
            # would cost the turn itself rather than its skills.
            log.warning("skillsearch: segment build failed (%s); no skills this turn", e)
            return None
        if not text:
            return None
        # Raven's after-turn feedback correlates skills shown against
        # skills used, which needs the ids that went in.
        return Segment(
            text=text,
            meta={
                "injected_skill_ids": [h.qualified_id for h in hits if getattr(h, "qualified_id", None)],
                "skill_hits_by_source": dict(Counter((h.meta or {}).get("source") or "?" for h in hits)),
            },
        )


class SkillSearchTool:
    """`skill_search` — the on-demand half of retrieval.

    The four members below are the ABC's *abstract* surface, and they are not
    enough on their own: ``Tool`` also carries concrete implementations the
    registry calls — ``to_schema``, ``cast_params``, ``validate_params``,
    ``display_call``, ``timeout_seconds``, ``blocking_interaction``. Duck-typing
    this class against the ABC therefore loads fine and then dies on the first
    turn with ``AttributeError: 'SkillSearchTool' object has no attribute
    'to_schema'``. :func:`make_skill_search_tool` subclasses the host's real
    ``Tool`` around this body instead, so the inherited half comes along.

    The body stays host-free so it can be imported, read and tested without a
    Raven checkout, which is the same reason ``SkillsSegment.build`` imports
    ``Segment`` at call time rather than at module scope.

    Two modes exist because two different things go wrong with one. Auto
    discovers capability the agent did not know to ask for, and pays for that
    on every turn. This one costs nothing until the agent reaches a step that
    needs a skill — and finds nothing if the agent never thinks to look, which
    is why the description below is written to say plainly when to reach for
    it, including for questions about in-house conventions. That wording is
    load-bearing: measured on a real host, a query about an internal template
    went unanswered until the description named that case.
    """

    def __init__(self, search: SkillSearch) -> None:
        self._search = search

    @property
    def name(self) -> str:
        return "skill_search"

    @property
    def description(self) -> str:
        return (
            "Search the skill library for a procedure that fits the task at "
            "hand, and get back the matching skills in full.\n\n"
            "A skill is a written workflow for a specific job — filling PDF "
            "forms, building a slide deck, migrating a schema — including the "
            "exact commands, files, and in-house conventions it needs.\n\n"
            "Reach for it when:\n"
            "- a task needs a multi-step procedure you would otherwise improvise;\n"
            "- a task names a format, tool, or workflow you would have to guess at;\n"
            "- a question asks about an internal convention, template, standard, "
            'or "our" way of doing something — a skill is where those are '
            "written down, so searching here comes before answering that you "
            "do not know.\n\n"
            "Search with the words the task actually uses; the query is matched "
            "against skill names and descriptions. Returns nothing when the "
            "library has no fit, which is a normal answer and means: proceed "
            "on your own."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "What you need to do, in the task's own words — e.g. "
                        '"extract tables from a scanned PDF invoice".'
                    ),
                },
            },
            "required": ["query"],
        }

    async def execute(self, **kwargs: Any) -> str:
        """Never raises: a failed search costs the turn a skill, not the turn."""
        query = str(kwargs.get("query") or "").strip()
        if not query:
            return "skill_search needs a query describing the task."
        try:
            hits = await self._search.hits(query, history=[])
            text = self._search.render(hits) if hits else ""
        except Exception as e:
            log.warning("skillsearch: skill_search failed (%s)", e)
            return "Skill search is unavailable right now. Proceed without a skill."
        # A miss is an answer. An empty string would read to a model as a
        # broken tool rather than as "the library has no fit".
        return text or f'No skill in the library matches "{query}". Proceed without one.'


def _resolve_model(cfg_map: dict[str, Any]) -> Any:
    """The channel the rewriter and the gate run on, or ``None``.

    Two sources, in order. Raven hands the *segment* factory a live
    ``LLMProvider`` under the private ``_provider`` key, which is the better
    one — it is the model the user already chose, and it follows a ``/model``
    switch. The *tool* factory is handed no such thing, so on-demand mode had
    nothing: `build_plugin_tools` passes the config slice and a
    ``ServiceLocator``, and a live object cannot be written in TOML.

    So a plugin-configured endpoint is the fallback, the same one every other
    host plugin already carries. Neither present means no rewriter and no
    gate, which is a real loss rather than a mild one: fusion ranks by
    position, so each source's best hit reaches the model however weakly it
    matched, and the gate is what removes those.
    """
    provider = cfg_map.get("_provider")
    if provider is not None:
        return _ProviderAdapter(provider)

    model_name = str(cfg_map.get("model") or "").strip()
    if not model_name:
        return None
    from .model import DEFAULT_BASE_URL, OpenAICompatibleModel

    return OpenAICompatibleModel(
        base_url=str(cfg_map.get("model_base_url") or DEFAULT_BASE_URL).strip(),
        api_key=str(cfg_map.get("model_api_key") or "").strip(),
        model=model_name,
        timeout_s=float(cfg_map.get("model_timeout_s") or 30.0),
    )


def _mode(cfg_map: dict[str, Any]) -> str:
    """``auto`` or ``on_demand``; anything unrecognised means the default.

    A typo should cost the deployment the mode it asked for, not its
    retrieval — so this narrows rather than raising.
    """
    return "auto" if str(cfg_map.get("mode", "")).strip() == "auto" else "on_demand"


def _build_search(ctx: Any) -> tuple[Any, dict[str, Any]] | None:
    """The engine and its config slice, or ``None`` when nothing is configured."""
    cfg_map = dict(getattr(ctx, "config", None) or {})
    services = getattr(ctx, "services", None)

    workspace = str(getattr(services, "workspace", ".") or ".")
    cfg_map.setdefault("workspace", workspace)
    cfg_map.setdefault("agent_id", getattr(services, "agent_id", "") or "")
    cfg_map.setdefault("hub_endpoint", "https://skillhub.evermind.ai")
    cfg_map.setdefault("clawhub_endpoint", "https://clawhub.ai")
    cfg_map.setdefault("skillhub_cn_endpoint", "https://api.skillhub.cn")
    # PathGuard placeholders' per-agent facts. Raven has no persistent
    # config/state root of its own, and the agent's writable home is the
    # workspace, so both {{HOME}} and {{AGENT_STATE_DIR}} collapse there.
    cfg_map.setdefault("output_dir", workspace)
    cfg_map.setdefault("home_dir", workspace)
    cfg_map.setdefault("state_dir", "")
    # Off by default: only a corpus produced by a trusted PathGuard pass may
    # expand placeholders onto this host's real filesystem paths.
    cfg_map.setdefault("resolve_placeholders", False)
    config = SearchConfig.from_mapping(cfg_map)

    # Raven passes live objects through the config slice under private
    # keys — they are objects, not user settings. `_store` reuses the
    # host's own SkillRegistry so the plugin does not rescan the disk the
    # host already watches; `_provider` is the model channel the rewriter
    # and the gate run on.
    model = _resolve_model(cfg_map)

    search = SkillSearch(
        config,
        model=model,
        store=cfg_map.get("_store"),
        get_tools=getattr(services, "get_tool_definitions", None),
    )
    if not search.has_sources:
        return None
    return search, cfg_map


def make_segment(ctx: Any) -> Any | None:
    """Context-segment entry point. ``None`` declines the stage.

    Declines in on-demand mode as well as when nothing is configured: filling
    the stage there would search a second time for the same turn and put the
    same skill in front of the model twice.
    """
    built = _build_search(ctx)
    if built is None:
        return None
    search, cfg_map = built
    if _mode(cfg_map) != "auto":
        return None
    return SkillsSegment(search)


def make_skill_search_tool(ctx: Any) -> Any | None:
    """Tool entry point. ``None`` declines to register.

    The host drops a tool whose factory returns ``None``, which is what keeps
    `skill_search` out of the agent's tool list in auto mode instead of
    offering a search that already ran.

    The returned object subclasses the host's own ``Tool``. That matters more
    than it looks: the registry calls ``to_schema`` to build the model-facing
    definition and ``cast_params`` / ``validate_params`` before dispatch, and
    the agent loop calls ``display_call`` with no ``hasattr`` guard — all of
    them concrete methods on the ABC that a look-alike class does not get. The
    import is here rather than at module scope because the host is not
    importable outside a checkout, and this package installs and tests without
    one.
    """
    built = _build_search(ctx)
    if built is None:
        return None
    search, cfg_map = built
    if _mode(cfg_map) != "on_demand":
        return None

    from raven.agent.tools.base import Tool  # host type, factory-local

    class _HostSkillSearchTool(SkillSearchTool, Tool):  # type: ignore[misc, valid-type]
        """`SkillSearchTool`'s body over the host's ABC."""

    return _HostSkillSearchTool(search)


def _text_of(resp: Any) -> str:
    """Pull the assistant text out of whatever shape the provider returns."""
    if isinstance(resp, str):
        return resp
    content = getattr(resp, "content", None)
    if isinstance(content, str):
        return content
    choices = getattr(resp, "choices", None) or (resp.get("choices") if isinstance(resp, dict) else None)
    if choices:
        first = choices[0]
        msg = getattr(first, "message", None) or (first.get("message") if isinstance(first, dict) else None)
        if msg is not None:
            text = getattr(msg, "content", None) or (msg.get("content") if isinstance(msg, dict) else None)
            if isinstance(text, str):
                return text
    return str(resp or "")


__all__ = ["SkillSearchTool", "SkillsSegment", "make_segment", "make_skill_search_tool"]
