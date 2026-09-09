"""The shared root and the host registry.

The acceptance list in the spec is mostly about what must *not* happen — a
user's `enabled` must not be undone, a broken file must not raise, a per-turn
caller must not write every turn — so most of these assert an absence.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from skillsearch import shared


@pytest.fixture
def registry(tmp_path: Path) -> Path:
    return tmp_path / "registry.json"


def test_the_root_is_the_same_expression_everywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(shared.HOME_ENV, raising=False)
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", "/home/x", 1))
    assert shared.shared_root() == Path("/home/x/.evermind-skillsearch")
    assert shared.shared_skills_dir() == Path("/home/x/.evermind-skillsearch/skills")
    assert shared.registry_path() == Path("/home/x/.evermind-skillsearch/registry.json")


def test_the_env_override_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(shared.HOME_ENV, str(tmp_path / "elsewhere"))
    assert shared.shared_root() == tmp_path / "elsewhere"


def test_registering_writes_an_absolute_path(registry: Path, tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    skills.mkdir()
    entries = shared.register_host("raven", skills, registry)
    assert [(e.id, e.dir, e.enabled) for e in entries] == [("raven", str(skills.resolve()), True)]
    assert json.loads(registry.read_text())["hosts"][0]["dir"] == str(skills.resolve())


def test_registering_again_with_the_same_path_does_not_write(registry: Path, tmp_path: Path) -> None:
    """The short circuit WorkBuddy's per-turn hook depends on.

    Asserted through the file's mtime rather than by counting calls: what
    matters is that the file on disk is untouched, however that is achieved.
    """
    skills = tmp_path / "skills"
    skills.mkdir()
    shared.register_host("workbuddy", skills, registry)
    before = registry.stat().st_mtime_ns
    os.utime(registry, ns=(before - 10_000_000_000, before - 10_000_000_000))
    stamped = registry.stat().st_mtime_ns

    shared.register_host("workbuddy", skills, registry)

    assert registry.stat().st_mtime_ns == stamped


def test_a_moved_directory_is_updated_but_keeps_the_users_enabled(registry: Path, tmp_path: Path) -> None:
    """The rule that makes the file editable.

    A user who has switched a host off must not have that undone the next time
    the host starts — which is exactly when the host re-registers.
    """
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    shared.register_host("hermes", first, registry)

    document = json.loads(registry.read_text())
    document["hosts"][0]["enabled"] = False
    registry.write_text(json.dumps(document))

    entries = shared.register_host("hermes", second, registry)

    assert len(entries) == 1
    assert entries[0].dir == str(second.resolve())
    assert entries[0].enabled is False


def test_one_entry_per_host_id(registry: Path, tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    shared.register_host("openclaw2", a, registry)
    shared.register_host("openclaw2", b, registry)
    assert len(shared.read_registry(registry)) == 1


def test_a_corrupt_registry_reads_as_empty_rather_than_raising(registry: Path) -> None:
    registry.write_text("{not json at all")
    assert shared.read_registry(registry) == []


def test_a_corrupt_registry_does_not_stop_a_host_registering(registry: Path, tmp_path: Path) -> None:
    """Acceptance 9: a broken registry costs sharing, not retrieval."""
    registry.write_text("]]]")
    skills = tmp_path / "skills"
    skills.mkdir()
    entries = shared.register_host("raven", skills, registry)
    assert [e.id for e in entries] == ["raven"]


def test_entries_without_an_id_or_a_dir_are_skipped(registry: Path) -> None:
    registry.write_text(
        json.dumps(
            {
                "hosts": [
                    {"id": "", "dir": "/x"},
                    {"id": "y"},
                    {"dir": "/z"},
                    "not a dict",
                    {"id": "ok", "dir": "/ok"},
                ]
            }
        )
    )
    assert [e.id for e in shared.read_registry(registry)] == ["ok"]


def test_disabled_and_missing_directories_are_not_scanned(registry: Path, tmp_path: Path) -> None:
    live, gone = tmp_path / "live", tmp_path / "gone"
    live.mkdir()
    registry.write_text(
        json.dumps(
            {
                "hosts": [
                    {"id": "a", "dir": str(live), "enabled": True},
                    {"id": "b", "dir": str(live), "enabled": False},
                    {"id": "c", "dir": str(gone), "enabled": True},
                ]
            }
        )
    )
    assert shared.registered_dirs("", registry) == [(str(live), "a")]


def test_a_host_does_not_scan_its_own_directory_twice(registry: Path, tmp_path: Path) -> None:
    mine, theirs = tmp_path / "mine", tmp_path / "theirs"
    mine.mkdir()
    theirs.mkdir()
    registry.write_text(
        json.dumps(
            {
                "hosts": [
                    {"id": "raven", "dir": str(mine)},
                    {"id": "hermes", "dir": str(theirs)},
                ]
            }
        )
    )
    assert shared.registered_dirs("raven", registry) == [(str(theirs), "hermes")]
    assert len(shared.registered_dirs("raven", registry, include_self=True)) == 2


def test_shared_dirs_registers_and_lists(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "root"
    monkeypatch.setenv(shared.HOME_ENV, str(root))
    (root / "skills").mkdir(parents=True)
    mine, theirs = tmp_path / "mine", tmp_path / "theirs"
    mine.mkdir()
    theirs.mkdir()
    shared.register_host("hermes", theirs, root / "registry.json")

    dirs = shared.shared_dirs("raven", mine, root / "registry.json")

    # The shared skills directory first, then other hosts; never our own.
    assert dirs == [(str(root / "skills"), "shared"), (str(theirs.resolve()), "hermes")]
    assert [e.id for e in shared.read_registry(root / "registry.json")] == ["hermes", "raven"]


def test_two_hosts_on_one_directory_is_scanned_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """OpenClaw 1 and 2 share `~/.openclaw/skills`; scanning it twice would
    double every skill in it."""
    root = tmp_path / "root"
    monkeypatch.setenv(shared.HOME_ENV, str(root))
    common = tmp_path / "openclaw-skills"
    common.mkdir()
    registry = root / "registry.json"
    shared.register_host("openclaw", common, registry)
    shared.register_host("openclaw2", common, registry)

    assert shared.shared_dirs("hermes", tmp_path / "hermes-skills", registry) == [
        (str(common.resolve()), "openclaw"),
    ]


def test_shared_dirs_never_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(shared.HOME_ENV, str(tmp_path / "root"))

    def explode(*_a: object, **_k: object) -> None:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(shared, "register_host", explode)
    assert shared.shared_dirs("raven", tmp_path) == []


# ---------------------------------------------------------------------------
# the cross-language contract


FIXTURES = Path(__file__).resolve().parents[2] / "engine-typescript" / "tests" / "fixtures-registry.json"


def _fixtures() -> dict:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _fixtures()["cases"], ids=lambda c: c["why"])
def test_registry_parses_the_same_as_the_typescript_port(case: dict, registry: Path) -> None:
    """Both ports read this file; a disagreement splits a user's agents apart.

    The fixture is shared with `engine-typescript/tests/parity.test.ts`, which
    asserts the same expectations against the same documents.
    """
    registry.write_text(json.dumps(case["document"]), encoding="utf-8")
    assert [e.as_json() for e in shared.read_registry(registry)] == case["expected"]


@pytest.mark.parametrize("text", _fixtures()["unparseable"])
def test_unparseable_registries_read_as_empty_in_both_ports(text: str, registry: Path) -> None:
    registry.write_text(text, encoding="utf-8")
    assert shared.read_registry(registry) == []


# ---------------------------------------------------------------------------
# noticing a changed shared directory without a restart


def test_the_watch_reports_no_change_on_its_first_look(tmp_path: Path) -> None:
    """Constructing a watch must not throw away a scan that was just built."""
    from skillsearch.watch import DirectoryWatch

    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "SKILL.md").write_text("---\nname: a\n---\n")
    watch = DirectoryWatch([tmp_path])
    assert watch.changed() is False
    assert watch.changed() is False


def test_the_watch_notices_an_added_edited_or_removed_skill(tmp_path: Path) -> None:
    from skillsearch.watch import DirectoryWatch

    first = tmp_path / "a"
    first.mkdir()
    skill = first / "SKILL.md"
    skill.write_text("---\nname: a\n---\n")
    watch = DirectoryWatch([tmp_path])
    watch.changed()

    second = tmp_path / "b"
    second.mkdir()
    (second / "SKILL.md").write_text("---\nname: b\n---\n")
    assert watch.changed() is True, "an added skill"
    assert watch.changed() is False, "and then it settles"

    os.utime(skill, ns=(1_000_000_000_000, 1_000_000_000_000))
    assert watch.changed() is True, "an edited skill"

    (second / "SKILL.md").unlink()
    assert watch.changed() is True, "a removed skill"


def test_the_watch_ignores_files_that_are_not_skills(tmp_path: Path) -> None:
    from skillsearch.watch import DirectoryWatch

    watch = DirectoryWatch([tmp_path])
    watch.changed()
    (tmp_path / "notes.md").write_text("not a skill")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "SKILL.md").write_text("---\nname: x\n---\n")
    assert watch.changed() is False


def test_an_empty_watch_is_free_and_silent(tmp_path: Path) -> None:
    """A host that opted out, or brought its own store, gets no walk."""
    from skillsearch.watch import DirectoryWatch

    watch = DirectoryWatch([])
    assert watch.active is False
    assert watch.changed() is False


def test_the_watch_never_raises_on_a_missing_directory(tmp_path: Path) -> None:
    from skillsearch.watch import DirectoryWatch

    watch = DirectoryWatch([tmp_path / "never-existed"])
    assert watch.changed() is False


@pytest.mark.asyncio
async def test_a_skill_dropped_into_the_shared_directory_is_found_next_turn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Acceptance 5, end to end through the engine.

    The user drags a skill into the shared directory while the agent is
    running. Before this, the scan was built once and kept for the life of the
    engine, so it took a restart — which for a *shared* library is fatal: a
    skill installed in one agent stayed invisible in the others.
    """
    from skillsearch import shared
    from skillsearch.config import SearchConfig
    from skillsearch.engine import SkillSearch

    root = tmp_path / "home"
    monkeypatch.setenv(shared.HOME_ENV, str(root))
    shared_skills = root / "skills"
    shared_skills.mkdir(parents=True)

    own = tmp_path / "own"
    own.mkdir()

    engine = SkillSearch(
        SearchConfig.from_mapping(
            {
                "skills_dir": str(own),
                "extra_dirs": [{"path": str(shared_skills), "name": "shared"}],
                "hub_endpoint": "",
                "clawhub_endpoint": "",
                "skillhub_cn_endpoint": "",
                "top_k": 3,
            }
        )
    )

    assert await engine.retrieve("extract tables from a scanned PDF invoice") == ""

    # The user drops one in. No restart, no invalidate() call by anyone.
    dropped = shared_skills / "pdf-tables"
    dropped.mkdir()
    (dropped / "SKILL.md").write_text(
        "---\nname: pdf-tables\n"
        "description: Extract tables from PDF documents, scanned or native, into CSV.\n"
        "---\n\nOCR scanned pages before extracting tables.\n",
        encoding="utf-8",
    )

    block = await engine.retrieve("extract tables from a scanned PDF invoice")
    assert "pdf-tables" in block
    # And exactly once — the shared directory is scanned by one source, not two.
    assert block.count("### Skill: pdf-tables") == 1
