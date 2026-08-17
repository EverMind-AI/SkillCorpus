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
