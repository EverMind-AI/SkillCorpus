"""When the gate runs, and why the default depends on the sources.

The gate is a precision instrument — its prompt tells the model to reject
when unsure — and the two sources want opposite things from it. A curated
local directory is better served by ranking alone, now that an unrelated
query returns nothing from it at all; a catalog of tens of thousands of
unvetted skills needs the environment check, because its best-ranked hit
for an unrelated query is still some hit.

So the default is neither on nor off: it is "on when a catalog is
configured". An explicit value always wins.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from skillsearch import SearchConfig, SkillSearch


def local_only(tmp_path: Path, **over: object) -> SearchConfig:
    skills = tmp_path / "skills"
    (skills / "pdf-forms").mkdir(parents=True, exist_ok=True)
    (skills / "pdf-forms" / "SKILL.md").write_text(
        "---\nname: pdf-forms\ndescription: Fill PDF acroforms\n---\n\nRun pdftk.\n"
    )
    return SearchConfig.from_mapping(
        {"skills_dir": str(skills), "workspace": str(tmp_path), **over}
    )


class Model:
    async def complete(self, *args: object, **kwargs: object) -> str:
        raise AssertionError("no model call was expected")


# ── the three-row default matrix ─────────────────────────────────────


def test_no_model_means_neither_step(tmp_path: Path) -> None:
    search = SkillSearch(local_only(tmp_path))
    assert search._gate is None
    assert search._rewriter is None


def test_local_only_leaves_the_gate_off(tmp_path: Path) -> None:
    """Ranking and top_k are enough for a directory the user curates."""
    search = SkillSearch(local_only(tmp_path, model="m"), model=Model())
    assert search._gate is None


def test_a_catalog_turns_the_gate_on(tmp_path: Path) -> None:
    """Unvetted skills need the environment check."""
    search = SkillSearch(
        local_only(tmp_path, model="m", hub_endpoint="http://catalog.invalid"), model=Model()
    )
    assert search._gate is not None


# ── the rewriter, which no longer has anything to veto ───────────────


def test_the_rewriter_is_on_wherever_a_model_is(tmp_path: Path) -> None:
    for over in ({}, {"hub_endpoint": "http://catalog.invalid"}):
        search = SkillSearch(local_only(tmp_path, model="m", **over), model=Model())
        assert search._rewriter is not None


# ── explicit values ──────────────────────────────────────────────────


@pytest.mark.parametrize("value", [True, "true", "1", "yes"])
def test_an_explicit_yes_wins_over_the_local_only_default(tmp_path: Path, value: object) -> None:
    search = SkillSearch(local_only(tmp_path, model="m", gate=value), model=Model())
    assert search._gate is not None


@pytest.mark.parametrize("value", [False, "false", "0", "no"])
def test_an_explicit_no_wins_over_the_catalog_default(tmp_path: Path, value: object) -> None:
    search = SkillSearch(
        local_only(tmp_path, model="m", hub_endpoint="http://catalog.invalid", gate=value),
        model=Model(),
    )
    assert search._gate is None


def test_the_unset_default_is_none_not_a_bool() -> None:
    """`gate=False` and "not configured" have to stay distinguishable."""
    assert SearchConfig().gate is None
    assert SearchConfig.from_mapping({}).gate is None
    assert SearchConfig(hub_endpoint="http://catalog.invalid").gate_enabled() is True
    assert SearchConfig().gate_enabled() is False
