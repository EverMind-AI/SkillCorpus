"""The segment against what Raven reads from it.

Raven is not importable outside a checkout, so the contract is pinned here
instead. The three class attributes matter more than they look: the host's
`ContextAssembler` sorts every builder by `order` while constructing
itself, so a segment missing them does not retrieve badly — it raises
during assembly and the agent never starts. Calling `build()` directly, as
the earlier tests did, skips that sort and cannot see the problem.
"""

from __future__ import annotations

import asyncio
import importlib.util
import tomllib
from pathlib import Path
from typing import Any

import pytest

import skillsearch_raven
from skillsearch_raven import (
    SkillSearchTool,
    SkillsSegment,
    _build_search,
    make_segment,
    make_skill_search_tool,
)

PLUGIN_ROOT = Path(skillsearch_raven.__file__).parent
MANIFEST = tomllib.loads((PLUGIN_ROOT / "raven-plugin.toml").read_text(encoding="utf-8"))


class Ctx:
    """The assembly context, reduced to what the segment reads."""

    def __init__(self, message: str) -> None:
        self.current_message = message
        self.session_messages: list[dict[str, Any]] = []


def write_skill(root: Path, name: str, description: str, body: str) -> None:
    (root / name).mkdir(parents=True)
    (root / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n", encoding="utf-8"
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    skills = tmp_path / "skills"
    write_skill(skills, "pdf-forms", "Fill PDF acroforms", "Run pdftk with an FDF.")
    write_skill(skills, "git-bisect", "Find the commit that broke a test", "Run git bisect start.")
    return tmp_path


class PluginContext:
    """What the host hands the factory: a config slice and a service locator."""

    def __init__(self, workspace: Path, config: dict[str, Any]) -> None:
        self.config = config
        self.services = type(
            "Services", (), {"workspace": str(workspace), "agent_id": "", "get_tool_definitions": None}
        )()


def segment_for(workspace: Path, **extra: Any) -> Any:
    # `mode` explicitly: the default is on demand, where the segment declines
    # its slot and `skill_search` does the work instead.
    return make_segment(
        PluginContext(
            workspace,
            {"skills_dir": str(workspace / "skills"), "top_k": 2, "mode": "auto", **extra},
        )
    )


# ── the protocol the assembler reads before building anything ────────────


def test_the_segment_carries_every_attribute_the_assembler_sorts_on() -> None:
    for attribute in ("name", "order", "needs_prefix"):
        assert hasattr(SkillsSegment, attribute), attribute


def test_it_claims_the_stage_its_manifest_claims() -> None:
    contribution = MANIFEST["plugin"]["contributes"]["context_segments"][0]
    assert SkillsSegment.name == contribution["name"] == contribution["replaces"] == "skills"


def test_it_takes_the_slot_reserved_for_the_skills_block() -> None:
    """Between the always-on skills (4) and the Curator (6)."""
    assert SkillsSegment.order == 5


def test_it_stays_in_the_parallel_phase() -> None:
    """Retrieval reads the current message, never the assembled prefix."""
    assert SkillsSegment.needs_prefix is False


def test_the_manifest_points_at_a_factory_that_resolves() -> None:
    module_path, _, attribute = MANIFEST["plugin"]["contributes"]["context_segments"][0]["factory"].partition(":")
    import importlib

    assert callable(getattr(importlib.import_module(module_path), attribute))


def test_the_manifest_id_matches_the_entry_point() -> None:
    import importlib.metadata as md

    points = {e.name: e.value for e in md.entry_points(group="raven.plugins")}
    assert points.get(MANIFEST["plugin"]["id"]) == "skillsearch_raven"


def test_the_manifest_enables_all_remote_sources_by_default() -> None:
    schema = MANIFEST["plugin"]["config_schema"]
    assert schema["hub_endpoint"]["default"] == "https://skillhub.evermind.ai"
    assert schema["clawhub_endpoint"]["default"] == "https://clawhub.ai"
    assert schema["skillhub_cn_endpoint"]["default"] == "https://api.skillhub.cn"


# ── behaviour ────────────────────────────────────────────────────────────

# `build()` wraps its result in the host's `Segment` type, so these need a
# Raven checkout. Against one, `verify-raven.py` in the repository root
# drives the same path further — `RAVEN_ROOT=<checkout> python
# verify-raven.py`, through the real `ContextAssembler`, to the assembled
# prompt.
needs_host = pytest.mark.skipif(importlib.util.find_spec("raven") is None, reason="needs a Raven checkout on the path")


@needs_host
def test_the_segment_returns_the_skill_the_turn_wants(workspace: Path) -> None:
    segment = segment_for(workspace)
    out = asyncio.run(segment.build(Ctx("fill in the acroform with pdftk")))
    text = getattr(out, "content", "") or str(out)
    assert "### Skill: pdf-forms" in text
    assert text.index("pdf-forms") < text.index("git-bisect")


@needs_host
def test_a_turn_sharing_no_term_with_the_corpus_gets_nothing(workspace: Path) -> None:
    segment = segment_for(workspace)
    out = asyncio.run(segment.build(Ctx("kubernetes ingress annotations")))
    assert out is None or not (getattr(out, "content", "") or "").strip()


def test_the_factory_declines_its_slot_when_nothing_is_configured(tmp_path: Path) -> None:
    """No skills directory and no catalog: the host keeps no fallback, so
    declining is what turns retrieval off rather than injecting nothing."""

    assert (
        make_segment(
            PluginContext(
                tmp_path,
                {
                    "skills_dir": str(tmp_path / "absent"),
                    "hub_endpoint": "",
                    "clawhub_endpoint": "",
                    "skillhub_cn_endpoint": "",
                }
            )
        )
        is None
    )


@needs_host
def test_build_never_raises_on_a_broken_engine(workspace: Path) -> None:
    """The hot path: a failure costs the turn its skills, not the turn."""
    segment = segment_for(workspace)

    class Exploding:
        async def hits(self, *args: Any, **kwargs: Any) -> list[Any]:
            raise RuntimeError("engine down")

        def render(self, hits: Any) -> str:
            raise AssertionError("render must not be reached after hits raised")

        @property
        def has_sources(self) -> bool:
            return True

    segment._search = Exploding()
    out = asyncio.run(segment.build(Ctx("fill in the acroform")))
    assert out is None or not (getattr(out, "content", "") or "").strip()


# ── the two modes ────────────────────────────────────────────────────────

# They are exclusive, and the manifest declares both factories: each one
# declines when it is not the configured mode, so exactly one is ever live.
# Filling the stage *and* offering the tool would search twice a turn and put
# the same skill in front of the model from two directions.


def bare_tool(workspace: Path, **extra: Any) -> SkillSearchTool:
    """The tool's body, without the host.

    `make_skill_search_tool` subclasses Raven's `Tool`, so it needs a checkout.
    What the body does — the description, a hit, a miss, an empty query — does
    not, and pinning it here keeps that covered wherever the suite runs.
    """
    built = _build_search(config_for(workspace, **extra))
    assert built is not None
    return SkillSearchTool(built[0])


def config_for(workspace: Path, **extra: Any) -> PluginContext:
    return PluginContext(
        workspace,
        {
            "skills_dir": str(workspace / "skills"),
            # 0.2.0 ships three catalog endpoints on by default; these tests
            # are about the mode switch, not about what a public catalog
            # returned this minute.
            "hub_endpoint": "",
            "clawhub_endpoint": "",
            "skillhub_cn_endpoint": "",
            **extra,
        },
    )


@needs_host
def test_the_default_is_on_demand(workspace: Path) -> None:
    assert make_segment(config_for(workspace)) is None
    tool = make_skill_search_tool(config_for(workspace))
    assert tool is not None
    assert tool.name == "skill_search"
    assert tool.parameters["required"] == ["query"]


def test_auto_is_the_mirror_image(workspace: Path) -> None:
    assert make_skill_search_tool(config_for(workspace, mode="auto")) is None
    assert make_segment(config_for(workspace, mode="auto")) is not None


@needs_host
def test_an_unrecognised_mode_falls_back_to_the_default(workspace: Path) -> None:
    assert make_skill_search_tool(config_for(workspace, mode="atuo")) is not None


def test_nothing_configured_registers_neither(tmp_path: Path) -> None:
    for mode in ("on_demand", "auto"):
        ctx = PluginContext(
            tmp_path,
            {"skills_dir": str(tmp_path / "absent"), "mode": mode,
             "hub_endpoint": "", "clawhub_endpoint": "", "skillhub_cn_endpoint": ""},
        )
        assert make_segment(ctx) is None, mode
        assert make_skill_search_tool(ctx) is None, mode


def test_the_tool_answers_a_hit_a_miss_and_an_empty_query(workspace: Path) -> None:
    tool = bare_tool(workspace, top_k=1)
    assert "pdf-forms" in asyncio.run(tool.execute(query="fill the acroform with pdftk"))

    # A miss is an answer, not an empty string a model would read as breakage.
    miss = asyncio.run(tool.execute(query="kubernetes ingress annotations"))
    assert "No skill" in miss and "Proceed without" in miss
    assert "needs a query" in asyncio.run(tool.execute(query="  "))


def test_the_description_says_when_to_reach_for_it(workspace: Path) -> None:
    """The only thing standing between on-demand mode and never retrieving.

    Measured on a real host: a question about an in-house template went
    unanswered until the description named that case explicitly.
    """
    description = bare_tool(workspace).description
    assert "multi-step" in description
    assert "internal convention" in description
    assert "Returns nothing" in description
    assert len(description) > 200


@needs_host
def test_the_tool_is_the_host_s_Tool_not_a_look_alike(workspace: Path) -> None:
    """Subclassing is the point, and only a checkout can prove it.

    `Tool` carries concrete implementations the host calls on every turn —
    `to_schema` to build the model-facing definition, `cast_params` and
    `validate_params` before dispatch, `display_call` from the agent loop with
    no `hasattr` guard. A class that merely defines the four abstract members
    loads fine and dies on the first turn with
    `AttributeError: 'SkillSearchTool' object has no attribute 'to_schema'`,
    which is exactly what a real Raven run produced before this.
    """
    from raven.agent.tools.base import Tool

    tool = make_skill_search_tool(config_for(workspace))
    assert isinstance(tool, Tool), "the factory must return the host's Tool"

    for inherited in ("to_schema", "cast_params", "validate_params", "display_call"):
        assert callable(getattr(tool, inherited, None)), inherited
    for attribute in ("timeout_seconds", "blocking_interaction"):
        assert hasattr(tool, attribute), attribute

    # The schema the registry actually hands the model.
    schema = tool.to_schema()
    assert "skill_search" in repr(schema)


# ── the model channel ────────────────────────────────────────────────────

# `build_plugin_tools` hands a factory the config slice and a `ServiceLocator`
# and nothing else, so the private `_provider` the segment factory gets never
# reaches the tool. On-demand — the default — therefore ran with no rewriter
# and no gate, which is not a mild degradation: fusion ranks by position, so
# each source's best hit reaches the model however weakly it matched.


def test_a_configured_endpoint_gives_on_demand_a_model(workspace: Path) -> None:
    from skillsearch_raven import _resolve_model

    model = _resolve_model({"model": "gpt-4o-mini", "model_base_url": "http://localhost:1/v1"})
    assert model is not None
    assert hasattr(model, "complete")


def test_a_live_provider_still_wins(workspace: Path) -> None:
    """The host's own provider is the better channel where it is offered:
    it is the model the user picked, and it follows a `/model` switch."""
    from skillsearch_raven import _ProviderAdapter, _resolve_model

    sentinel = object()
    model = _resolve_model({"_provider": sentinel, "model": "ignored"})
    assert isinstance(model, _ProviderAdapter)


def test_naming_no_model_leaves_retrieval_raw(workspace: Path) -> None:
    """Not an error — a local directory with no catalog does not need a gate."""
    from skillsearch_raven import _resolve_model

    assert _resolve_model({}) is None
    assert _resolve_model({"model_base_url": "http://localhost:1/v1"}) is None


def test_the_endpoint_keys_are_declared_in_the_manifest() -> None:
    # Unlike a JSON-Schema host, Raven ignores keys its manifest omits, so an
    # undeclared setting is silently dropped rather than rejected.
    schema = MANIFEST["plugin"]["config_schema"]
    for key in ("model", "model_base_url", "model_api_key", "model_timeout_s"):
        assert key in schema, key


def test_unknown_mode_narrows_to_the_default_and_says_so(caplog):
    """A typo must not cost retrieval, and must not pass unnoticed either.

    Narrowing is deliberate: `mode: "atuo"` should leave the deployment with
    working retrieval, not a failed plugin load. But it lands on the opposite
    mode from the one that was typed, and 0.3.0 changed which mode the default
    is — so silence here is how an operator ends up with auto injection they
    asked for and never got.
    """
    import logging

    from skillsearch_raven import _mode

    with caplog.at_level(logging.WARNING, logger="skillsearch_raven"):
        assert _mode({"mode": "atuo"}) == "on_demand"
    assert any("unknown mode" in record.message for record in caplog.records), caplog.records
    assert any("atuo" in record.getMessage() for record in caplog.records)


def test_a_recognised_mode_is_quiet(caplog):
    import logging

    from skillsearch_raven import _mode

    with caplog.at_level(logging.WARNING, logger="skillsearch_raven"):
        assert _mode({"mode": "auto"}) == "auto"
        assert _mode({"mode": "on_demand"}) == "on_demand"
        assert _mode({}) == "on_demand"
    assert [r for r in caplog.records if "unknown mode" in r.message] == []
