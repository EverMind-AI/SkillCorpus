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
from skillsearch_raven import SkillsSegment, make_segment

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
    return make_segment(PluginContext(workspace, {"skills_dir": str(workspace / "skills"), "top_k": 2, **extra}))


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
