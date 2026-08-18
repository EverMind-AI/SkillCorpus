"""Root layering: which copy of a duplicated skill reaches the index."""

from __future__ import annotations

from pathlib import Path

from skillsearch.local_store import DirectorySkillStore


def write_skill(root: Path, name: str, body: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n\n{body}\n")


def test_an_earlier_root_shadows_a_later_one(tmp_path: Path) -> None:
    user, bundled = tmp_path / "user", tmp_path / "bundled"
    write_skill(user, "pdf-forms", "user copy")
    write_skill(bundled, "pdf-forms", "bundled copy")

    store = DirectorySkillStore([(user, "local"), (bundled, "builtin")])
    skills = store.list_all()

    assert len(skills) == 1, "both copies reached the index; layering does nothing"
    assert skills[0].source == "local"
    assert "user copy" in skills[0].content


def test_distinct_names_from_several_roots_all_survive(tmp_path: Path) -> None:
    user, bundled = tmp_path / "user", tmp_path / "bundled"
    write_skill(user, "pdf-forms", "a")
    write_skill(bundled, "git-bisect", "b")

    store = DirectorySkillStore([(user, "local"), (bundled, "builtin")])
    assert {s.name for s in store.list_all()} == {"pdf-forms", "git-bisect"}


def test_invalidate_picks_up_a_skill_written_after_the_first_scan(tmp_path: Path) -> None:
    write_skill(tmp_path, "first", "a")
    store = DirectorySkillStore([(tmp_path, "local")])
    assert len(store.list_all()) == 1

    write_skill(tmp_path, "second", "b")
    assert len(store.list_all()) == 1, "the cache should hold until invalidated"

    store.invalidate()
    assert len(store.list_all()) == 2


def test_the_index_text_is_name_twice_and_description(tmp_path) -> None:
    """The line the TypeScript `formatSkillText` must match byte for byte.

    The body is out by default: the description is what the `SKILL.md`
    format asks authors to write the trigger conditions into, and it is
    also what the gate reads, so indexing it alone keeps ranking and gating
    looking at the same text.
    """
    from skillsearch.local_pool import _format_skill_text
    from skillsearch.types import SkillMeta

    meta = SkillMeta(
        name="pdf-tables", description="Extract tables.", content="x" * 5000,
        source="local", path="", always=False,
    )
    assert _format_skill_text(meta) == "pdf-tables pdf-tables Extract tables."


def test_index_body_restores_the_capped_body(tmp_path) -> None:
    from skillsearch.local_pool import _format_skill_text
    from skillsearch.types import SkillMeta

    meta = SkillMeta(
        name="pdf-tables", description="Extract tables.", content="x" * 5000,
        source="local", path="", always=False,
    )
    assert _format_skill_text(meta, index_body=True) == (
        "pdf-tables pdf-tables Extract tables. " + "x" * 4000
    )


async def test_a_term_only_in_a_body_is_findable_once_index_body_is_on(tmp_path) -> None:
    """The cost of the new default, stated as a test rather than as prose."""
    from skillsearch import SearchConfig, SkillSearch

    skills = tmp_path / "skills"
    (skills / "invoicing").mkdir(parents=True)
    (skills / "invoicing" / "SKILL.md").write_text(
        "---\nname: invoicing\ndescription: Bill a customer\n---\n\nRun qhjklz to reconcile.\n"
    )

    def search_for(index_body: bool) -> SkillSearch:
        return SkillSearch(
            SearchConfig.from_mapping({
                "skills_dir": str(skills), "workspace": str(tmp_path),
                "index_body": index_body,
            })
        )

    assert "invoicing" not in await search_for(False).retrieve("qhjklz")
    assert "invoicing" in await search_for(True).retrieve("qhjklz")
