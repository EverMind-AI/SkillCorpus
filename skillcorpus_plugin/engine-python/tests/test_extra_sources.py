"""A source this package does not know about, fused with the ones it does.

`extra_sources` is the whole extension story: a host with a memory backend,
a private library or a second catalog writes it against `SkillSource` and
passes it in, and the engine never learns what it is. That used to be a
built-in source with the host's name on it, which meant the engine imported
a concept belonging to one deployment.

What this pins is the seam, not any particular source: that an outside
source reaches fusion, that it alone can make retrieval configured, and
that the pipeline keeps degrading rather than raising when it misbehaves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from skillsearch.config import SearchConfig
from skillsearch.engine import SkillSearch
from skillsearch.types import RouterHit, SkillSource


@dataclass
class HostSource:
    """What a host writes: a name, a weight, and one async method."""

    name: str = "host"
    weight: float = 0.9
    hits: list[RouterHit] = field(default_factory=list)
    calls: list[tuple[str, int]] = field(default_factory=list)

    async def search(self, query: str, history: list[Any], k: int) -> list[RouterHit]:
        self.calls.append((query, k))
        return self.hits[:k]


def hit(name: str, body: str, source: str = "host") -> RouterHit:
    return RouterHit(
        qualified_id=f"{source}/{name}",
        name=name,
        content=body,
        score=1.0,
        meta={"source": source},
    )


def write_skill(root: Path, name: str, description: str, body: str) -> None:
    (root / name).mkdir(parents=True, exist_ok=True)
    (root / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n", encoding="utf-8"
    )


def config(**over: Any) -> SearchConfig:
    return SearchConfig.from_mapping({"skills_dir": "", "hub_endpoint": "", **over})


def test_the_published_protocol_is_the_shape_a_host_implements() -> None:
    assert isinstance(HostSource(), SkillSource)


async def test_an_outside_source_alone_makes_retrieval_configured() -> None:
    """No skills directory and no catalog: without this the engine is off."""
    search = SkillSearch(config(), extra_sources=[HostSource(hits=[hit("lint-first", "Run the linter.")])])
    assert search.has_sources
    assert "lint-first" in await search.retrieve("how do I land a change")


async def test_an_outside_source_is_searched_with_the_query_and_a_bound() -> None:
    source = HostSource(hits=[hit(f"note-{i}", f"body {i}") for i in range(10)])
    search = SkillSearch(config(top_k=3, over_fetch=2), extra_sources=[source])

    await search.retrieve("anything")

    assert source.calls[-1][0] == "anything"
    assert source.calls[-1][1] == 6, "k is top_k * over_fetch, as for every other source"


async def test_it_is_fused_beside_the_local_source_rather_than_replacing_it(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    write_skill(skills, "pdf-forms", "Fill PDF acroforms", "Run pdftk with an FDF.")
    search = SkillSearch(
        config(skills_dir=str(skills), workspace=str(tmp_path)),
        extra_sources=[HostSource(hits=[hit("acroform-notes", "Notes on acroforms.")])],
    )

    block = await search.retrieve("fill in the acroform")

    assert "pdf-forms" in block
    assert "acroform-notes" in block


async def test_a_broken_outside_source_costs_the_turn_its_skills_not_the_turn(tmp_path: Path) -> None:
    class Broken:
        name = "broken"
        weight = 0.9

        async def search(self, query: str, history: list[Any], k: int) -> list[RouterHit]:
            raise RuntimeError("host source down")

    skills = tmp_path / "skills"
    write_skill(skills, "pdf-forms", "Fill PDF acroforms", "Run pdftk with an FDF.")
    search = SkillSearch(config(skills_dir=str(skills), workspace=str(tmp_path)), extra_sources=[Broken()])

    # The router swallows a failing source; the local one still answers.
    assert "pdf-forms" in await search.retrieve("fill in the acroform")


def test_the_engine_carries_no_host_specific_source() -> None:
    """The regression this file replaced: a source named for one deployment.

    `SkillSearch(memory=...)`, `SearchConfig.agent_id` and `weight_memory`
    are gone; a host that wants recall over its own skills writes the
    adapter and passes it through `extra_sources`.
    """
    import inspect

    assert "memory" not in inspect.signature(SkillSearch.__init__).parameters
    for gone in ("agent_id", "weight_memory"):
        assert not hasattr(SearchConfig(), gone), gone
