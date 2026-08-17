"""The memory source against a backend written from the published protocol.

The point of this file is the fake below: it implements
:class:`~skillsearch.ports.MemoryRecall` exactly as documented and nothing
else. If the source ever calls ``recall`` with a different shape, the fake
raises ``TypeError``, the router swallows it as an empty result, and the
first assertion here fails — which is the only way a signature drift shows
up at all, since nothing else about it is loud.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from skillsearch.ports import MemoryRecall
from skillsearch.sources.everos_source import EverosSkillSource


@dataclass
class Record:
    text: str
    metadata: dict[str, Any] | None = None


@dataclass
class ProtocolBackend:
    """Implements the protocol as published. No tolerant **kwargs."""

    records: list[Record] = field(default_factory=list)
    calls: list[tuple[str, str, int]] = field(default_factory=list)

    async def recall(self, query: str, *, agent_id: str, top_k: int) -> list[Record]:
        self.calls.append((query, agent_id, top_k))
        return self.records[:top_k]


def test_the_published_protocol_is_the_shape_the_source_calls() -> None:
    assert isinstance(ProtocolBackend(), MemoryRecall)


async def test_a_protocol_backend_returns_hits(tmp_path) -> None:
    backend = ProtocolBackend(
        records=[
            Record("Always run the linter before pushing.", {"id": "m1", "name": "lint-first"}),
            Record("Prefer rebase over merge on this repo.", {"id": "m2"}),
        ]
    )
    source = EverosSkillSource(backend, agent_id="agent-1")

    hits = await source.search("how do I land a change", [], k=5)

    assert backend.calls == [("how do I land a change", "agent-1", 5)]
    assert hits[0].qualified_id == "everos/m1"
    assert hits[0].name == "lint-first"
    # A record with no name still gets one, derived from its text.
    assert hits[1].name


async def test_k_is_passed_through_as_top_k() -> None:
    backend = ProtocolBackend(records=[Record(f"note {i}") for i in range(10)])
    source = EverosSkillSource(backend, agent_id="agent-1")

    hits = await source.search("anything", [], k=3)

    assert backend.calls[-1][2] == 3
    assert len(hits) == 3


async def test_a_backend_that_raises_is_not_the_source_s_problem_to_hide() -> None:
    """The source itself propagates; the router is what degrades to empty."""

    class Broken:
        async def recall(self, query: str, *, agent_id: str, top_k: int) -> list[Record]:
            raise RuntimeError("backend down")

    source = EverosSkillSource(Broken(), agent_id="agent-1")
    with pytest.raises(RuntimeError):
        await source.search("anything", [], k=5)
