"""Raven adapter — a context segment.

Raven assembles its prompt from an ordered list of named stages and lets a
plugin claim one by name. Retrieval claims ``skills``.

Ship alongside a ``raven-plugin.toml``::

    [plugin]
    id                 = "skillsearch"
    version            = "0.1.0"
    bundled            = true
    enabled_by_default = true

    [[plugin.contributes.context_segments]]
    name     = "skills"
    factory  = "skillsearch.adapters.raven:make_segment"
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
    """A Raven ``SegmentBuilder`` over :class:`SkillSearch`."""

    def __init__(self, search: SkillSearch, heading: str = "# Skills") -> None:
        self._search = search
        self._heading = heading

    async def build(self, ctx: Any) -> Any:
        from raven.context_engine.base import Segment  # host type, adapter-local

        query = getattr(ctx, "current_message", "") or ""
        history = list(getattr(ctx, "session_messages", None) or [])
        hits = await self._search.hits(query, history=history)
        if not hits:
            return None
        text = self._search.render(hits)
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


def make_segment(ctx: Any) -> Any | None:
    """Plugin entry point. ``None`` declines the stage."""
    cfg_map = dict(getattr(ctx, "config", None) or {})
    services = getattr(ctx, "services", None)

    workspace = str(getattr(services, "workspace", ".") or ".")
    cfg_map.setdefault("workspace", workspace)
    cfg_map.setdefault("agent_id", getattr(services, "agent_id", "") or "")
    config = SearchConfig.from_mapping(cfg_map)

    # Raven passes its live provider and memory backend through the config
    # slice under private keys — they are objects, not user settings.
    provider = cfg_map.get("_provider")
    model = _ProviderAdapter(provider) if provider is not None else None

    search = SkillSearch(
        config,
        model=model,
        store=cfg_map.get("_store"),
        memory=cfg_map.get("_memory"),
        get_tools=getattr(services, "get_tool_definitions", None),
    )
    if search._router is None:
        return None
    return SkillsSegment(search, heading=config.heading)


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


__all__ = ["SkillsSegment", "make_segment"]
