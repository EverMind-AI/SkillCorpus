"""Hermes adapter — the engine behind a memory provider's ``prefetch``.

Hermes drives a selected memory provider through a fixed pipeline; the hook
that matters here is ``prefetch(query, *, session_id="") -> str``, called
before each model call, whose return value the runtime injects into that
turn. The memory slot is simply the slot that hook is routed through —
nothing here writes memory.

This module is the engine side. The packaged plugin that registers it —
manifest, provider subclass, configuration UI — is ``plugin-hermes/`` in
this repository; install that rather than assembling one by hand. A host
registers a provider with ``ctx.register_memory_provider(...)``.

Two contract points Hermes states and this adapter honours: ``is_available``
makes no network calls, and nothing on the hot path is allowed to raise.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from skillsearch.config import SearchConfig
from skillsearch.engine import SkillSearch

log = logging.getLogger(__name__)

CONFIG_FILENAME = "skillsearch.json"


class SkillSearchEngine:
    """Retrieval behind Hermes's per-turn ``prefetch`` hook.

    Hermes calls ``prefetch`` synchronously, while the engine is async.
    Rather than block the caller's loop, the work runs on a private loop in
    a background thread with a hard deadline — a slow catalog costs the
    turn its skills, never the turn itself.
    """

    name = "skillsearch"

    def __init__(self, search: SkillSearch, *, timeout_s: float = 8.0) -> None:
        self._search = search
        self._timeout_s = timeout_s
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    # ── Hermes lifecycle ─────────────────────────────────────────────

    @classmethod
    def from_hermes(cls, ctx: Any, *, hermes_home: str | None = None) -> SkillSearchEngine:
        home = hermes_home or os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
        config = load_config(home)
        model = getattr(ctx, "model_client", None) or getattr(ctx, "llm", None)
        return cls(
            SkillSearch(
                config,
                model=_wrap_model(model) if model is not None else None,
                get_tools=getattr(ctx, "get_tool_names", None),
            ),
            timeout_s=load_prefetch_timeout(home),
        )

    def is_available(self) -> bool:
        """No network calls, per the contract — configuration only."""
        return self._search.has_sources

    def initialize(self, session_id: str = "", hermes_home: str | None = None) -> None:
        self._ensure_loop()

    def system_prompt_block(self) -> str:
        return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Recall for this turn. Returns ``""`` rather than raising."""
        if not (query or "").strip() or not self.is_available():
            return ""
        try:
            loop = self._ensure_loop()
            future = asyncio.run_coroutine_threadsafe(self._search.retrieve(query), loop)
            try:
                return future.result(timeout=self._timeout_s)
            except TimeoutError:
                # Stop the work, not just the waiting. The coroutine runs
                # on a loop this adapter owns, so an abandoned one keeps
                # its model call and its HTTP connections alive; against a
                # slow backend, one per turn accumulates.
                future.cancel()
                raise
        except TimeoutError:
            log.warning("skillsearch: prefetch exceeded %.0fs; injecting nothing", self._timeout_s)
            return ""
        except Exception as e:
            log.warning("skillsearch: prefetch failed (%s)", e)
            return ""

    def sync_turn(self, *args: Any, **kwargs: Any) -> None:
        """Retrieval reads; it has nothing to capture."""

    def shutdown(self) -> None:
        loop = self._loop
        if loop is None:
            return
        with contextlib.suppress(Exception):
            asyncio.run_coroutine_threadsafe(self._search.aclose(), loop).result(timeout=2)
        loop.call_soon_threadsafe(loop.stop)
        self._loop = None

    # ── Sync/async bridge ────────────────────────────────────────────

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is not None:
            return self._loop
        ready = threading.Event()

        def _run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            ready.set()
            loop.run_forever()

        self._thread = threading.Thread(target=_run, name="skillsearch", daemon=True)
        self._thread.start()
        ready.wait(timeout=5)
        if self._loop is None:
            raise RuntimeError("skillsearch: could not start its event loop")
        return self._loop


def load_config(hermes_home: str) -> SearchConfig:
    """Read ``$HERMES_HOME/skillsearch.json``.

    Same placement and format as every other Hermes memory provider's
    config file, so ``hermes memory setup``-style tooling and hand-editing
    both work the way a Hermes user already expects.
    """
    path = Path(hermes_home) / CONFIG_FILENAME
    raw: dict[str, Any] = {}
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("skillsearch: cannot read %s (%s); using defaults", path, e)
            raw = {"hub_endpoint": "", "clawhub_endpoint": "", "skillhub_cn_endpoint": ""}
    raw.setdefault("workspace", hermes_home)
    raw.setdefault("skills_dir", str(Path(hermes_home) / "skills"))
    raw.setdefault("hub_endpoint", "https://skillhub.evermind.ai")
    raw.setdefault("clawhub_endpoint", "https://clawhub.ai")
    raw.setdefault("skillhub_cn_endpoint", "https://api.skillhub.cn")
    # PathGuard placeholders' per-agent facts. Hermes's own config/state root
    # is `$HERMES_HOME` (the workspace here), and its home is the process home.
    raw.setdefault("output_dir", hermes_home)
    raw.setdefault("home_dir", str(Path.home()))
    raw.setdefault("state_dir", hermes_home)
    # Off by default: only a trusted PathGuard corpus may expand placeholders.
    raw.setdefault("resolve_placeholders", False)
    return SearchConfig.from_mapping(raw)


DEFAULT_PREFETCH_TIMEOUT_S = 8.0


def load_prefetch_timeout(hermes_home: str) -> float:
    """Read ``timeout_s`` from ``$HERMES_HOME/skillsearch.json``.

    Read from the raw file rather than through :func:`load_config`, because
    this bounds the adapter's own wait and so is not a
    :class:`SearchConfig` field — routing it through one would silently
    drop it, which is what used to happen: the Hermes plugin offers
    ``timeout_s`` in its setup and writes it to the same file, and every
    value a user chose was discarded in favour of the default.
    """
    path = Path(hermes_home) / CONFIG_FILENAME
    if not path.is_file():
        return DEFAULT_PREFETCH_TIMEOUT_S
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        value = float(raw.get("timeout_s", DEFAULT_PREFETCH_TIMEOUT_S))
    except Exception as e:
        log.warning("skillsearch: cannot read timeout_s from %s (%s)", path, e)
        return DEFAULT_PREFETCH_TIMEOUT_S
    return value if value > 0 else DEFAULT_PREFETCH_TIMEOUT_S


def _wrap_model(client: Any) -> Any:
    """Adapt a Hermes model client to the ``ChatModel`` protocol."""

    class _Wrapped:
        async def complete(
            self,
            messages: list[dict[str, str]],
            *,
            model: str | None = None,
            temperature: float = 0.0,
            max_tokens: int = 8192,
        ) -> str:
            fn = getattr(client, "complete", None) or getattr(client, "chat", None)
            if fn is None:
                raise RuntimeError("model client exposes neither complete() nor chat()")
            out = fn(messages=messages, model=model, temperature=temperature, max_tokens=max_tokens)
            if asyncio.iscoroutine(out):
                out = await out
            return out if isinstance(out, str) else str(getattr(out, "content", out))

    return _Wrapped()


__all__ = ["CONFIG_FILENAME", "SkillSearchEngine", "load_config"]
