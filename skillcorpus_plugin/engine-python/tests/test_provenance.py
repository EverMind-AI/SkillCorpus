"""Installed skills: where they land, why they count once, and managing them.

Spec sections 3, 4 and 5. They are tested together because they only work
together: installing into a scanned directory (3) is what creates the double
counting that identity dedup (4) exists to prevent, and the marker that
carries the identity is also the ledger management (5) reads.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skillsearch import provenance
from skillsearch.config import SearchConfig
from skillsearch.engine import SkillSearch
from skillsearch.fusion import rrf_merge_weighted
from skillsearch.types import RouterHit


def _origin(source: str = "hub", slug: str = "pdf-tables", version: str = "1.0") -> provenance.Origin:
    return provenance.Origin(
        origin=provenance.identity(source, slug),
        source=source,
        slug=slug,
        version=version,
        sha256=provenance.body_digest("body"),
        installed_at=provenance.now(),
    )


def _install(
    root: Path,
    source: str = "hub",
    slug: str = "pdf-tables",
    version: str = "1.0",
    body: str = "OCR scanned pages first.",
) -> Path:
    skill = provenance.slug_dir(root, source, slug)
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {slug}\ndescription: Extract tables from PDF documents into CSV.\n---\n\n{body}\n",
        encoding="utf-8",
    )
    provenance.write_marker(skill, _origin(source, slug, version))
    return skill


# ---------------------------------------------------------------------------
# the marker


def test_a_marker_round_trips(tmp_path: Path) -> None:
    skill = tmp_path / "skill"
    skill.mkdir()
    written = _origin()
    assert provenance.write_marker(skill, written) is True
    read = provenance.read_marker(skill)
    assert read == written


def test_a_skill_without_a_marker_is_not_an_error(tmp_path: Path) -> None:
    """A hand-written skill has no marker and must keep working exactly."""
    skill = tmp_path / "handwritten"
    skill.mkdir()
    assert provenance.read_marker(skill) is None


@pytest.mark.parametrize("text", ["{not json", "[]", "null", '{"source": "hub"}', '{"slug": "x"}'])
def test_an_unusable_marker_reads_as_absent(tmp_path: Path, text: str) -> None:
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / provenance.MARKER).write_text(text, encoding="utf-8")
    assert provenance.read_marker(skill) is None


def test_a_slug_cannot_escape_the_install_root(tmp_path: Path) -> None:
    """Slugs come from a catalogue and reach the filesystem."""
    for slug in ("../../etc/passwd", "a/b", "..", "~/x", ""):
        landed = provenance.slug_dir(tmp_path, "hub", slug)
        assert landed.parent == tmp_path, slug
        assert tmp_path in landed.resolve().parents or landed.resolve().parent == tmp_path


# ---------------------------------------------------------------------------
# identity dedup — section 4


def test_fusion_collapses_a_local_copy_with_the_catalogue_entry() -> None:
    """The whole reason installing into a scanned directory is safe.

    `qualified_id` cannot do this — `local/pdf-tables` and `hub/pdf-tables`
    are different ids — and a body digest cannot either, since the installed
    copy and the catalogue's summary rarely have identical bytes.
    """
    origin = provenance.identity("hub", "pdf-tables")
    local = RouterHit(
        qualified_id="local/pdf-tables",
        name="pdf-tables",
        content="body",
        score=1.0,
        meta={"source": "local", "origin": origin},
    )
    remote = RouterHit(
        qualified_id="hub/pdf-tables",
        name="pdf-tables",
        content="",
        score=0.9,
        meta={"source": "hub", "origin": origin},
    )

    merged = rrf_merge_weighted([("local", 1.0, [local]), ("hub", 1.0, [remote])], k=5, dedup_by="qualified_id")

    assert len(merged) == 1
    assert sorted(merged[0].meta["contributing_sources"]) == ["hub", "local"]


def test_fusion_keeps_two_different_skills_apart() -> None:
    a = RouterHit(
        qualified_id="local/a", name="a", content="", score=1.0, meta={"origin": provenance.identity("hub", "a")}
    )
    b = RouterHit(
        qualified_id="local/b", name="b", content="", score=1.0, meta={"origin": provenance.identity("hub", "b")}
    )
    assert len(rrf_merge_weighted([("local", 1.0, [a, b])], k=5, dedup_by="qualified_id")) == 2


def test_a_skill_without_an_origin_falls_back_to_the_old_key() -> None:
    """Every hand-written skill, and every uninstalled catalogue hit."""
    a = RouterHit(qualified_id="local/x", name="x", content="", score=1.0, meta={})
    b = RouterHit(qualified_id="hub/x", name="x", content="", score=1.0, meta={})
    assert len(rrf_merge_weighted([("local", 1.0, [a]), ("hub", 1.0, [b])], k=5, dedup_by="qualified_id")) == 2
    assert len(rrf_merge_weighted([("local", 1.0, [a]), ("hub", 1.0, [b])], k=5, dedup_by="name")) == 1


# ---------------------------------------------------------------------------
# the ledger — section 5


def test_listing_walks_the_directory_rather_than_an_index(tmp_path: Path) -> None:
    _install(tmp_path, "hub", "pdf-tables")
    _install(tmp_path, "clawhub", "git-bisect")
    (tmp_path / "handwritten").mkdir()
    (tmp_path / "handwritten" / "SKILL.md").write_text("---\nname: h\n---\n")

    listed = provenance.list_installed(tmp_path)

    # Only what this plugin installed, and the user's own skill untouched.
    assert [o.origin for o in listed] == ["clawhub/git-bisect", "hub/pdf-tables"]


def test_a_skill_deleted_by_hand_simply_disappears(tmp_path: Path) -> None:
    """The directory is the truth — no stale row nobody can explain."""
    import shutil

    skill = _install(tmp_path)
    assert len(provenance.list_installed(tmp_path)) == 1
    shutil.rmtree(skill)
    assert provenance.list_installed(tmp_path) == []


def test_uninstalling_removes_it_and_reports_whether_it_was_there(tmp_path: Path) -> None:
    _install(tmp_path)
    assert provenance.uninstall(tmp_path, "hub/pdf-tables") is True
    assert provenance.list_installed(tmp_path) == []
    assert provenance.uninstall(tmp_path, "hub/pdf-tables") is False


# ---------------------------------------------------------------------------
# replacing safely — section 5's update rule


def test_an_update_replaces_in_place(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    _install(root, version="1.0", body="old")
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "SKILL.md").write_text("---\nname: pdf-tables\n---\n\nnew\n", encoding="utf-8")
    provenance.write_marker(staging, _origin(version="2.0"))

    provenance.swap_into_place(staging, provenance.slug_dir(root, "hub", "pdf-tables"))

    listed = provenance.list_installed(root)
    assert [(o.origin, o.version) for o in listed] == [("hub/pdf-tables", "2.0")]
    # One directory per identity: an update replaces rather than accumulating
    # a second ranked copy of the same skill.
    assert len([p for p in root.iterdir() if p.is_dir()]) == 1


def test_a_failed_update_leaves_the_old_version_working(tmp_path: Path) -> None:
    """Acceptance 8. The whole point of switching rather than overwriting."""
    root = tmp_path / "skills"
    root.mkdir()
    _install(root, version="1.0", body="the version that works")
    dest = provenance.slug_dir(root, "hub", "pdf-tables")

    with pytest.raises(OSError):
        provenance.swap_into_place(tmp_path / "never-extracted", dest)

    assert (dest / "SKILL.md").read_text(encoding="utf-8").endswith("the version that works\n")
    assert [(o.origin, o.version) for o in provenance.list_installed(root)] == [("hub/pdf-tables", "1.0")]


# ---------------------------------------------------------------------------
# end to end, through the engine


@pytest.mark.asyncio
async def test_an_installed_skill_is_retrieved_once_not_twice(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Acceptance 2, the case sections 3 and 4 exist for together.

    Before identity dedup, a skill installed into a scanned directory was both
    a local hit and a remote hit and took two of the ranking's slots.
    """
    from skillsearch import shared

    monkeypatch.setenv(shared.HOME_ENV, str(tmp_path / "home"))
    shared_skills = shared.shared_skills_dir()
    shared_skills.mkdir(parents=True)
    _install(shared_skills, "hub", "pdf-tables")

    class Catalogue:
        """A source that keeps offering what is already installed."""

        name = "hub"
        weight = 1.0

        async def search(self, query: str, history: list, k: int) -> list[RouterHit]:
            del query, history, k
            return [
                RouterHit(
                    qualified_id="hub/pdf-tables",
                    name="pdf-tables",
                    content="",
                    score=0.9,
                    meta={"source": "hub", "origin": provenance.identity("hub", "pdf-tables")},
                )
            ]

    engine = SkillSearch(
        SearchConfig.from_mapping(
            {
                "skills_dir": str(tmp_path / "own"),
                "extra_dirs": [{"path": str(shared_skills), "name": "shared"}],
                "hub_endpoint": "",
                "clawhub_endpoint": "",
                "skillhub_cn_endpoint": "",
                "top_k": 5,
            }
        ),
        extra_sources=[Catalogue()],
    )

    block = await engine.retrieve("extract tables from a scanned PDF invoice")

    assert "pdf-tables" in block
    assert block.count("### Skill: pdf-tables") == 1, block


def test_the_marker_is_json_a_person_can_read(tmp_path: Path) -> None:
    """Installing puts files on a user's disk; they must be able to see what."""
    skill = _install(tmp_path)
    document = json.loads((skill / provenance.MARKER).read_text(encoding="utf-8"))
    assert document["origin"] == "hub/pdf-tables"
    assert document["source"] == "hub"
    assert document["skill_version"] == "1.0"
    assert "_note" in document and "uninstall" in document["_note"]


def test_a_bundle_that_wraps_the_skill_is_still_listed_and_removable(tmp_path: Path) -> None:
    """The shape a real catalogue actually sends.

    Hub bundles wrap the whole skill in one directory, so the `SKILL.md` — and
    the marker beside it, which is where the scanner reads it — sits one level
    below the directory the install created. Listing only the top level found
    nothing, so dedup worked while every management call was blind. Caught by
    running against the live catalogue; no hand-built fixture has a wrapper.
    """
    dest = provenance.slug_dir(tmp_path, "hub", "extract-tables-from-pdf")
    body = dest / "extract-tables-from-pdf"
    body.mkdir(parents=True)
    (body / "SKILL.md").write_text("---\nname: extract-tables-from-pdf\n---\n\nbody\n", encoding="utf-8")
    provenance.write_marker(body, _origin(slug="extract-tables-from-pdf"))

    assert [o.origin for o in provenance.list_installed(tmp_path)] == ["hub/extract-tables-from-pdf"]
    # The *outer* directory, or uninstalling leaves an empty husk behind.
    assert provenance.find_installed(tmp_path, "hub/extract-tables-from-pdf") == dest
    assert provenance.uninstall(tmp_path, "hub/extract-tables-from-pdf") is True
    assert not dest.exists()
    assert provenance.list_installed(tmp_path) == []


def test_a_skill_bundled_inside_another_skill_is_not_treated_as_an_install(tmp_path: Path) -> None:
    """One level down, not arbitrary depth.

    Otherwise a skill that ships another skill in its own tree could be
    uninstalled out from under the one that owns it.
    """
    outer = tmp_path / "handwritten"
    deep = outer / "vendor" / "nested"
    deep.mkdir(parents=True)
    provenance.write_marker(deep, _origin(slug="nested"))
    assert provenance.list_installed(tmp_path) == []


FIXTURES = Path(__file__).resolve().parents[2] / "engine-typescript" / "tests" / "fixtures-slugdir.json"


@pytest.mark.parametrize("case", json.loads(FIXTURES.read_text(encoding="utf-8"))["cases"], ids=lambda c: c["dir"])
def test_slug_dir_matches_the_typescript_port(case: dict, tmp_path: Path) -> None:
    """Both ports install into the same directory on one machine.

    A disagreement puts one skill in two directories, which is exactly the
    duplication identity dedup exists to prevent — and it stayed invisible
    because each suite only ever compared a port with itself.
    """
    assert provenance.slug_dir(tmp_path, case["source"], case["slug"]).name == case["dir"]


def test_uninstalling_leaves_a_record(tmp_path: Path) -> None:
    """Acceptance 7's middle clause, which was missing entirely.

    Installing puts files on someone's disk. Removing them has to leave
    something behind, or a user asking "what did this thing ever put here"
    has no way to find out, and a skill that vanished is indistinguishable
    from one that was never installed.
    """
    root = tmp_path / "skills"
    root.mkdir()
    _install(root, "hub", "pdf-tables", "1.0")

    assert provenance.uninstall(root, "hub/pdf-tables") is True

    # Beside the registry, not inside `skills/` — the scanner walks that.
    log = provenance.uninstall_log(root)
    assert log == tmp_path / "uninstalled.log"
    records = provenance.read_uninstalled(root)
    assert len(records) == 1
    assert records[0]["origin"] == "hub/pdf-tables"
    assert records[0]["skill_version"] == "1.0"
    assert records[0]["removed_at"]
    assert records[0]["installed_at"]


def test_the_log_is_appended_never_rewritten(tmp_path: Path) -> None:
    """An append-only file cannot lose an earlier entry to a crash."""
    root = tmp_path / "skills"
    root.mkdir()
    for slug in ("a", "b", "c"):
        _install(root, "hub", slug)
        provenance.uninstall(root, f"hub/{slug}")
    assert [r["origin"] for r in provenance.read_uninstalled(root)] == ["hub/a", "hub/b", "hub/c"]


def test_a_corrupt_line_costs_one_record_not_the_history(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    _install(root, "hub", "a")
    provenance.uninstall(root, "hub/a")
    log = provenance.uninstall_log(root)
    log.write_text(log.read_text() + "{not json\n" + '{"origin": "hub/b"}\n', encoding="utf-8")
    assert [r["origin"] for r in provenance.read_uninstalled(root)] == ["hub/a", "hub/b"]


def test_a_removal_that_did_not_happen_is_not_recorded(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    assert provenance.uninstall(root, "hub/never-installed") is False
    assert provenance.read_uninstalled(root) == []


@pytest.mark.asyncio
async def test_an_uninstalled_skill_stops_being_retrievable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Acceptance 7's last clause, asserted by retrieving rather than by
    looking at the disk — the ledger being empty and the engine still
    answering are different facts."""
    from skillsearch import shared

    monkeypatch.setenv(shared.HOME_ENV, str(tmp_path / "home"))
    shared_skills = shared.shared_skills_dir()
    shared_skills.mkdir(parents=True)
    _install(shared_skills, "hub", "pdf-tables")

    engine = SkillSearch(
        SearchConfig.from_mapping(
            {
                "skills_dir": str(tmp_path / "own"),
                "extra_dirs": [{"path": str(shared_skills), "name": "shared"}],
                "hub_endpoint": "",
                "clawhub_endpoint": "",
                "skillhub_cn_endpoint": "",
                "top_k": 5,
            }
        )
    )
    assert "pdf-tables" in await engine.retrieve("extract tables from a PDF into CSV")

    assert provenance.uninstall(shared_skills, "hub/pdf-tables") is True

    # No restart, no invalidate() by hand — the directory watch notices.
    assert "pdf-tables" not in await engine.retrieve("extract tables from a PDF into CSV")
    assert [r["origin"] for r in provenance.read_uninstalled(shared_skills)] == ["hub/pdf-tables"]
