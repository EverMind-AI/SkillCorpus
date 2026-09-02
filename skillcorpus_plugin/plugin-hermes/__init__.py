"""Skill retrieval as a Hermes memory provider.

Hermes drives the pipeline and calls `prefetch` before each model call,
injecting whatever string comes back into that turn. That is the whole
integration: this module answers that one call with the skills the turn
wants, and does nothing else.

Registered in the memory slot because that is the slot Hermes routes
`prefetch` through. Nothing here writes memory — `sync_turn` and the
session hooks are deliberately absent, so a deployment can run this
alongside its own memory backend only by choosing which one occupies the
slot. Two providers cannot both hold it.

Everything fails open. `prefetch` sits between the user's message and the
model's reply, so a broken catalog or an unreachable provider costs the
turn its skills and never the turn itself.

Install: this plugin needs the `skillsearch` package importable in the
same environment (`pip install skillsearch`), and its own configuration in
`$HERMES_HOME/skillsearch.json`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

try:  # the real ABC exists inside a Hermes runtime
    from agent.memory_provider import MemoryProvider
except Exception:  # unit tests and standalone tooling run outside Hermes

    class MemoryProvider:  # type: ignore[no-redef]
        pass


logger = logging.getLogger(__name__)

CONFIG_FILENAME = "skillsearch.json"

DEFAULTS: dict[str, Any] = {
    "skills_dir": "~/.hermes/skills",
    "hub_endpoint": "https://skillhub.evermind.ai",
    "clawhub_endpoint": "https://clawhub.ai",
    "skillhub_cn_endpoint": "https://api.skillhub.cn",
    "model": "",
    "top_k": "2",
    "max_select": "2",
    "timeout_s": "8.0",
    # How skills reach the agent. "on_demand" offers a `skill_search` tool and
    # lets the agent decide; "auto" injects what it finds before every model
    # call. See SKILL_SEARCH_SCHEMA for why the default is the former.
    "mode": "on_demand",
}


SKILL_SEARCH_SCHEMA: dict[str, Any] = {
    "name": "skill_search",
    "description": (
        "Search the skill library for a procedure that fits the task at hand, "
        "and get back the matching skills in full.\n\n"
        "A skill is a written workflow for a specific job — filling PDF forms, "
        "building a slide deck, migrating a schema — including the exact "
        "commands, files, and in-house conventions it needs.\n\n"
        "Reach for it when:\n"
        "- a task needs a multi-step procedure you would otherwise improvise;\n"
        "- a task names a format, tool, or workflow you would have to guess at;\n"
        "- a question asks about an internal convention, template, standard, or "
        '"our" way of doing something — a skill is where those are written '
        "down, so searching here comes before answering that you do not know."
        "\n\nSearch with the words the task actually uses; the query is matched "
        "against skill names and descriptions. Returns nothing when the library "
        "has no fit, which is a normal answer and means: proceed on your own."
    ),
    "parameters": {
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
    },
}


def load_config(hermes_home: str | None) -> dict[str, Any]:
    """Read `$HERMES_HOME/skillsearch.json`.

    Args:
        hermes_home: the host's home directory, or None before the host
            has told us where it is.

    Returns:
        The stored values, or an empty mapping when the file is missing or
        unreadable. A malformed config disables retrieval rather than
        stopping the agent from starting.
    """
    if not hermes_home:
        return {}
    path = Path(hermes_home).expanduser() / CONFIG_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as err:
        logger.warning("[skillsearch] unreadable %s (%s); retrieval is off", path, err)
        return {}
    return data if isinstance(data, dict) else {}


class SkillSearchProvider(MemoryProvider):
    """Retrieval behind Hermes's per-turn `prefetch` hook.

    Built lazily: `initialize` is the first call that knows where
    `$HERMES_HOME` is, and the engine holds a scan and an HTTP pool that
    should outlive a turn, so it is constructed once there and reused.
    """

    def __init__(self, host: Any = None) -> None:
        self._host = host
        self._adapter: Any = None
        self._home: str | None = None
        # `_mode` is read on every turn; this keeps an unrecognised value to
        # one log line instead of one per turn.
        self._warned_mode = False

    @property
    def name(self) -> str:
        return "skillsearch"

    # -- lifecycle ------------------------------------------------------------

    def is_available(self) -> bool:
        """Whether this provider can be selected. No network, per the host contract."""
        try:
            import skillsearch  # noqa: F401
        except Exception:
            logger.warning("[skillsearch] the skillsearch package is not importable")
            return False
        return True

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        """Build the engine for this session.

        Args:
            session_id: the host's session identity. Retrieval is
                stateless, so this is not stored; it is accepted because
                the host passes it.
            kwargs: the host's extras; `hermes_home` locates the config.
        """
        del session_id
        self._home = kwargs.get("hermes_home")
        self._adapter = None
        try:
            from .engine_adapter import SkillSearchEngine

            self._adapter = SkillSearchEngine.from_hermes(
                _EngineContext(self._host, load_config(self._home)),
                hermes_home=self._home,
            )
        except Exception as err:  # a bad config must not stop the agent booting
            logger.warning("[skillsearch] retrieval disabled (%s)", err)

    def shutdown(self) -> None:
        adapter, self._adapter = self._adapter, None
        if adapter is not None:
            try:
                adapter.shutdown()
            except Exception as err:
                logger.warning("[skillsearch] shutdown (%s)", err)

    # -- recall (hot path; before each model call) ----------------------------

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """The skills this turn wants, rendered for injection.

        Args:
            query: the user's message for this turn.
            session_id: the host's session identity, unused — retrieval
                searches the query and nothing else.

        Returns:
            The block to inject, or `""` when this turn gets no skills.
            Never raises: this runs before the model answers the user.
        """
        del session_id
        # On demand, this hook stays silent and `skill_search` does the work.
        # Running both would search twice a turn and put the same skill in
        # front of the model from two directions.
        if self._mode != "auto":
            return ""
        return self._search(query)

    def _search(self, query: str) -> str:
        """Retrieval, shared by both modes. Never raises."""
        if self._adapter is None:
            return ""
        try:
            return self._adapter.prefetch(query)
        except Exception as err:
            logger.warning("[skillsearch] recall failed (fail-open): %s", err)
            return ""

    # -- host configuration UI (`hermes memory setup`) -------------------------

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """The `skill_search` tool, in on-demand mode only.

        In auto mode retrieval already ran before this model call, so offering
        a tool that would run it again is a second search for the same turn.
        """
        return [] if self._mode == "auto" else [SKILL_SEARCH_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs: Any) -> str:
        """Run `skill_search`. Returns text for the model, never raises."""
        del kwargs
        if tool_name != SKILL_SEARCH_SCHEMA["name"]:
            return f"Unknown tool: {tool_name}"
        query = str((args or {}).get("query") or "").strip()
        if not query:
            return "skill_search needs a query describing the task."
        block = self._search(query)
        # A miss is a normal answer, and has to read as one: an empty string
        # would look to a model like a broken tool rather than "no fit".
        return block or f'No skill in the library matches "{query}". Proceed without one.'

    @property
    def _mode(self) -> str:
        """``auto`` or ``on_demand``; anything unrecognised means the default.

        Narrowed rather than raised — a typo should cost the deployment the
        mode it asked for, not its retrieval — but logged, because handing
        back the opposite mode with no signal is the failure shape this plugin
        family keeps getting caught by, and 0.3.0 changed which mode the
        default is. Logged once per instance: this property is read on every
        turn and a line per turn is noise, not a signal.
        """
        raw = load_config(self._home).get("mode")
        asked = "" if raw is None else str(raw).strip()
        if asked not in ("", "auto", "on_demand"):
            if not self._warned_mode:
                self._warned_mode = True
                logger.warning("[skillsearch] unknown mode %r; running in on_demand", asked)
            return "on_demand"
        return "auto" if asked == "auto" else "on_demand"

    def get_config_schema(self) -> list[dict[str, Any]]:
        """The fields `hermes memory setup` prompts for."""
        return [
            {
                "key": "skills_dir",
                "description": "Directory scanned for SKILL.md files",
                "default": DEFAULTS["skills_dir"],
            },
            {
                "key": "hub_endpoint",
                "description": "Remote catalog base URL (leave empty for local only)",
            },
            {
                "key": "hub_api_key",
                "description": "Bearer token for the remote catalog",
            },
            {
                "key": "model",
                "description": (
                    "Model for the rewriter and the relevance gate. "
                    "Empty runs retrieval unfiltered, which injects a weak match "
                    "rather than nothing"
                ),
            },
            {
                "key": "top_k",
                "description": "Upper bound on skills injected per turn",
                "default": DEFAULTS["top_k"],
            },
            {
                "key": "max_select",
                "description": "Upper bound on what the gate keeps",
                "default": DEFAULTS["max_select"],
            },
            {
                "key": "timeout_s",
                "description": "Deadline for one retrieval, in seconds",
                "default": DEFAULTS["timeout_s"],
            },
        ]

    def save_config(self, values: dict[str, Any], hermes_home: str) -> None:
        """Merge answered values into `$HERMES_HOME/skillsearch.json`.

        Args:
            values: what setup collected; `None` entries are left alone so
                skipping a prompt keeps the stored value.
            hermes_home: the host's home directory.
        """
        path = Path(hermes_home).expanduser() / CONFIG_FILENAME
        existing = load_config(hermes_home)
        existing.update({k: v for k, v in values.items() if v is not None})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")


class _EngineContext:
    """What the engine adapter reads, assembled from host and config.

    The adapter takes the host's model client when the host exposes one.
    Hermes may not, and unfiltered retrieval is a real cost — fusion ranks
    by position, so an unrelated turn still gets a skill — so a configured
    endpoint stands in when the host offers nothing.
    """

    def __init__(self, host: Any, config: dict[str, Any]) -> None:
        self._host = host
        self._config = config

    @property
    def model_client(self) -> Any:
        for attribute in ("model_client", "llm"):
            client = getattr(self._host, attribute, None)
            if client is not None:
                return client
        model = str(self._config.get("model") or "")
        if not model:
            return None
        from .model import DEFAULT_BASE_URL, OpenAICompatibleModel

        return OpenAICompatibleModel(
            base_url=str(self._config.get("model_base_url") or DEFAULT_BASE_URL),
            api_key=str(self._config.get("model_api_key") or ""),
            model=model,
        )

    @property
    def get_tool_names(self) -> Any:
        """The host's tool list, which the gate uses to drop what this agent cannot run."""
        return getattr(self._host, "get_tool_names", None)


def register(ctx: Any) -> None:
    """Claim the memory slot with the retrieval provider.

    Args:
        ctx: the host's plugin context, carried so the provider can reach
            a model client and the agent's tool names if it exposes them.
    """
    ctx.register_memory_provider(SkillSearchProvider(ctx))
