"""Weighted RRF: what it ranks by, and which copy of a collision survives.

Both are the same question — fusion exists because per-source scores are
not comparable numbers — so these tests are written to fail if any
comparison ever reaches for ``hit.score`` again.
"""

from __future__ import annotations

from skillsearch.fusion import RRF_K, rrf_merge_weighted
from skillsearch.types import RouterHit


def hit(qualified_id: str, name: str, score: float, content: str = "") -> RouterHit:
    return RouterHit(
        qualified_id=qualified_id,
        name=name,
        content=content,
        score=score,
        meta={},
    )


def test_rank_beats_raw_score_within_one_source() -> None:
    # `far` carries a hugely larger raw score but sits second in its source.
    merged = rrf_merge_weighted(
        [("local", 1.0, [hit("local/near", "near", 0.01), hit("local/far", "far", 999.0)])],
        k=5,
    )
    assert [h.name for h in merged] == ["near", "far"]


def test_a_skill_two_sources_agree_on_outranks_either_alone() -> None:
    merged = rrf_merge_weighted(
        [
            ("local", 1.0, [hit("local/solo", "solo", 9.0), hit("local/both", "both", 1.0)]),
            ("hub", 1.0, [hit("hub/both", "both", 0.5), hit("hub/other", "other", 0.4)]),
        ],
        k=5,
    )
    assert merged[0].name == "both"


def test_the_representative_is_the_better_ranked_copy_not_the_higher_scored() -> None:
    """The collision rule that raw scores would get wrong.

    BM25 is unbounded and a catalog's quality score sits in 0..1, so the
    local copy wins any raw-score comparison by construction. Here the hub
    ranked the skill first and local ranked it third, so the hub copy is
    the better-ranked one and must be what the prompt shows — even though
    its raw score is two orders of magnitude smaller.
    """
    merged = rrf_merge_weighted(
        [
            (
                "local",
                1.0,
                [
                    hit("local/a", "a", 8.0),
                    hit("local/b", "b", 7.0),
                    hit("local/shared", "shared", 6.0, content="local copy"),
                ],
            ),
            ("hub", 1.0, [hit("hub/shared", "shared", 0.9, content="hub copy")]),
        ],
        k=5,
    )
    shared = next(h for h in merged if h.name == "shared")
    assert shared.content == "hub copy"


def test_a_collision_sums_both_contributions() -> None:
    merged = rrf_merge_weighted(
        [
            ("local", 1.0, [hit("local/x", "x", 1.0)]),
            ("hub", 0.5, [hit("hub/x", "x", 1.0)]),
        ],
        k=5,
    )
    assert len(merged) == 1
    expected = 1.0 / (RRF_K + 1) + 0.5 / (RRF_K + 1)
    assert merged[0].meta["rrf_score"] == expected
    assert merged[0].meta["contributing_sources"] == ["local", "hub"]


def test_weight_orders_two_sources_that_rank_equally() -> None:
    merged = rrf_merge_weighted(
        [
            ("local", 1.0, [hit("local/x", "x", 0.0)]),
            ("hub", 0.5, [hit("hub/y", "y", 99.0)]),
        ],
        k=5,
    )
    assert [h.name for h in merged] == ["x", "y"]


def test_dedup_by_qualified_id_keeps_same_named_hits_apart() -> None:
    merged = rrf_merge_weighted(
        [
            ("local", 1.0, [hit("local/x", "same", 1.0)]),
            ("hub", 1.0, [hit("hub/x", "same", 1.0)]),
        ],
        k=5,
        dedup_by="qualified_id",
    )
    assert len(merged) == 2


def test_k_bounds_the_result() -> None:
    hits = [hit(f"local/{i}", f"s{i}", 1.0) for i in range(10)]
    assert len(rrf_merge_weighted([("local", 1.0, hits)], k=3)) == 3


def test_no_sources_fuse_to_nothing() -> None:
    assert rrf_merge_weighted([], k=5) == []
    assert rrf_merge_weighted([("local", 1.0, [])], k=5) == []


def test_the_rrf_offset_reaches_fusion_from_config() -> None:
    """`rrf_k` was added to both fusion functions but wired only on the
    TypeScript side, so no Python host had a way to set it.

    Checked by behaviour rather than by attribute: a smaller offset widens
    the gap between adjacent ranks, which is the whole reason a host fusing
    a short head wants one.
    """
    import asyncio

    from skillsearch.router import SkillForgeRouter
    from skillsearch.types import RouterHit

    class Source:
        def __init__(self, name: str, weight: float, names: list[str]) -> None:
            self.name, self.weight, self._names = name, weight, names

        async def search(self, query: str, history: list, k: int) -> list[RouterHit]:
            return [
                RouterHit(qualified_id=f"{self.name}/{n}", name=n, content=n, score=1.0, meta={"source": self.name})
                for n in self._names[:k]
            ]

    sources = [Source("local", 1.0, ["a", "b"]), Source("hub", 0.85, ["c", "d"])]

    def fuse(rrf_k: int) -> list[float]:
        # `score` is the per-source score the hit arrived with; the fused
        # value is annotated onto meta, which is what the offset moves.
        router = SkillForgeRouter(sources, rrf_k=rrf_k)
        return [h.meta["rrf_score"] for h in asyncio.run(router.select("q", [], k=4))]

    wide, narrow = fuse(10), fuse(60)
    assert wide[0] - wide[1] > narrow[0] - narrow[1]


def test_the_default_offset_is_still_the_paper_s() -> None:
    from skillsearch.config import SearchConfig
    from skillsearch.fusion import RRF_K

    assert SearchConfig().rrf_k == RRF_K == 60
