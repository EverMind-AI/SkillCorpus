"""Installing, against the real EverMind SkillHub.

The other half of the acceptance list. `e2e_shared.py` proves two agents see
each other's directories; this proves the part that puts something in one —
retrieve from a live catalogue, keep what came back, and count it once.

It talks to the real hub rather than a fake because the failure this guards
against is precisely a real catalogue's shape: a hit whose slug, version and
bundle layout are whatever the service decided, arriving through the real
download and extract path. A hand-built fixture agrees with itself.

Covers, from the spec's list:

    1  a retrieved skill appears under `<shared root>/skills/`
    2  the next turn finds it locally, **exactly once**, with no restart
    7  uninstalling removes it, and it stops being retrievable

    8  a failed update leaves the previous version installed and working

Acceptance 8 is here after all. The unit tests drive `swap_into_place` with a
staging directory that does not exist, which proves the primitive; this drives
a real update of a really-installed skill whose download really fails, which
is the path a user hits. The two are not the same test — everything between
"decide to update" and "swap" is only covered by the second.

Usage:

    python e2e_install.py                       # local-only assertions
    python e2e_install.py --openclaw /path/to/openclaw --generation 2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _e2e

#: The default in every host's config, and a service that answers without a
#: key. Overridable so this can be pointed at a staging deployment.
HUB = os.environ.get("SKILLSEARCH_E2E_HUB", "https://skillhub.evermind.ai")

#: Words that match a real skill in that catalogue. Checked against the live
#: service by `--probe` before anything is asserted, so a catalogue that
#: changed its contents fails loudly rather than looking like a broken plugin.
QUERY = "extract tables from a PDF"


def engine_for(home: Path, skills: Path, top_k: int = 3):
    from skillsearch.config import SearchConfig
    from skillsearch.engine import SkillSearch

    return SkillSearch(SearchConfig.from_mapping({
        "skills_dir": str(skills),
        "hub_endpoint": HUB,
        # One catalogue at a time: two would make "which source installed it"
        # a guess, and this is about the install path, not about fusion.
        "clawhub_endpoint": "",
        "skillhub_cn_endpoint": "",
        "top_k": top_k,
        # No model, so no gate and no rewriter: this measures installing, and
        # a gate that decides the turn wants no skills measures nothing.
        "model": "",
        "gate": False,
    }))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine-python", type=Path,
                    default=Path(__file__).resolve().parents[3] / "engine-python")
    ap.add_argument("--dump", type=Path, default=None)
    args = ap.parse_args()

    sys.path.insert(0, str(args.engine_python))
    from skillsearch import provenance, shared

    home = Path(tempfile.mkdtemp(prefix="skillsearch-install-"))
    os.environ[shared.HOME_ENV] = str(home)
    own = home / "own-skills"
    own.mkdir()
    shared_skills = shared.shared_skills_dir()

    results: dict = {"home": str(home), "hub": HUB}
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str) -> None:
        results[name] = {"pass": ok, "detail": detail}
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        print(f"        {detail}")
        if not ok:
            failures.append(name)

    print(f"hub: {HUB}\nshared home: {home}")

    def skill_name(directory: Path) -> str:
        """The name the renderer prints, which is not the install slug.

        The slug is sanitised from the catalogue's own id — here
        `mzlzyCA_html-markdown_extract-tables-from-pdf` — while the block says
        `### Skill: extract-tables-from-pdf`, from the body's frontmatter.
        Counting occurrences of the wrong one of those is how this script first
        reported a working dedup as broken.
        """
        for candidate in (directory, *sorted(directory.iterdir())):
            skill_md = candidate / "SKILL.md"
            if not skill_md.is_file():
                continue
            for line in skill_md.read_text(encoding="utf-8", errors="replace").splitlines()[:10]:
                if line.startswith("name:"):
                    return line.split(":", 1)[1].strip()
        return ""

    async def run() -> dict:
        """Everything in one event loop.

        The engine holds an HTTP client bound to the loop that first used it,
        so a second `asyncio.run` kills the catalogue source with
        `Event loop is closed` — which silently turns the dedup check into a
        local-only check that cannot fail. Real hosts have one loop; so does
        this.
        """
        engine = engine_for(home, own)
        first = await engine.retrieve(QUERY)
        if not first.strip():
            return {"empty": True}

        installed = provenance.list_installed(shared_skills)
        if not installed:
            return {"first": first, "installed": []}

        second = await engine.retrieve(QUERY)
        fresh = engine_for(home, own)
        third = await fresh.retrieve(QUERY)
        return {"first": first, "installed": installed, "second": second, "third": third}

    turns = asyncio.run(run())

    if turns.get("empty"):
        check("the catalogue returned something for the probe query", False,
              f"empty block for {QUERY!r} — the catalogue's contents may have changed")
        print(f"failed: {failures}")
        return 1

    # -- Acceptance 1 -----------------------------------------------------
    installed = turns["installed"]
    check("a retrieved skill is kept under the shared skills directory",
          bool(installed),
          f"{[o.origin for o in installed]} under {shared_skills}")
    if not installed:
        print(f"failed: {failures}")
        return 1

    origin = installed[0].origin
    outer = provenance.find_installed(shared_skills, origin)
    # The marker sits beside the `SKILL.md`, which a wrapped bundle puts one
    # level below the directory the install created — so it is read through
    # the same resolution the ledger uses, not by guessing a path.
    ledger = [o.origin for o in provenance.list_installed(shared_skills)]
    check("and it carries a readable provenance marker",
          outer is not None and origin in ledger,
          f"{origin} version {installed[0].version!r} at {outer}")

    # -- Acceptance 2 ------------------------------------------------------
    name = skill_name(outer) if outer else ""
    heading = f"### Skill: {name}"
    check("the next turn finds it exactly once, not twice",
          name != "" and turns["second"].count(heading) == 1,
          f"{heading!r} appears {turns['second'].count(heading)}x — the installed copy and "
          f"the catalogue hit must collapse on identity {origin!r}")
    check("a newly built engine sees it too, and still once",
          name != "" and turns["third"].count(heading) == 1,
          f"{turns['third'].count(heading)}x in an engine with no cached scan")

    # -- Acceptance 8: an update that fails leaves the old one working -----
    #
    # A real installed skill, a real update attempt, a download that really
    # fails. The unit tests cover the swap primitive; nothing covered the
    # decision path in front of it, which is where a half-written directory
    # would come from.
    before_dir = provenance.find_installed(shared_skills, origin)
    before_body = ""
    if before_dir is not None:
        for candidate in (before_dir, *sorted(p for p in before_dir.iterdir() if p.is_dir())):
            if (candidate / "SKILL.md").is_file():
                before_body = (candidate / "SKILL.md").read_text(encoding="utf-8")
                break

    failed_as_expected = False
    if before_dir is not None:
        # Same skill, a version the marker does not have, and an endpoint that
        # nothing is listening on — so the update is attempted and the download
        # is what fails.
        dead = f"http://127.0.0.1:{_e2e.dead_port()}"

        async def attempt() -> None:
            from skillsearch.hub_client import SkillHubClient

            client = SkillHubClient(dead, cache_dir=home / "cache", install_root=shared_skills)
            try:
                await client.install(installed[0].slug,
                                     prefetched_meta={"slug": installed[0].slug,
                                                      "version": "999.0",
                                                      "skill_md": ""})
            finally:
                await client.aclose()

        try:
            asyncio.run(attempt())
        except Exception:  # the failure is the point
            failed_as_expected = True

    after_dir = provenance.find_installed(shared_skills, origin)
    after_body = ""
    if after_dir is not None:
        for candidate in (after_dir, *sorted(p for p in after_dir.iterdir() if p.is_dir())):
            if (candidate / "SKILL.md").is_file():
                after_body = (candidate / "SKILL.md").read_text(encoding="utf-8")
                break

    check("a failed update leaves the previous version installed and working",
          failed_as_expected and after_dir == before_dir and after_body == before_body
          and bool(before_body),
          f"download failed as expected: {failed_as_expected}; "
          f"directory unchanged: {after_dir == before_dir}; "
          f"body unchanged: {after_body == before_body} ({len(before_body)} chars)")

    # No half-written directory left behind, which is the other way this can
    # go wrong: a staging directory that survives is picked up by the scanner.
    leftovers = sorted(p.name for p in shared_skills.iterdir()
                       if p.is_dir() and (".incoming-" in p.name or ".retiring-" in p.name))
    check("and no half-written directory is left behind",
          not leftovers,
          f"leftovers: {leftovers}" if leftovers else "none")

    # -- Acceptance 7 ------------------------------------------------------
    removed = [o.origin for o in installed if provenance.uninstall(shared_skills, o.origin)]
    check("uninstalling reports that it removed what was there",
          removed == [o.origin for o in installed],
          f"removed {removed}")
    check("and nothing is left in the ledger or on disk",
          provenance.list_installed(shared_skills) == []
          and not any(p.is_dir() for p in shared_skills.iterdir()),
          f"remaining: {sorted(p.name for p in shared_skills.iterdir())}")

    if args.dump:
        args.dump.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    shutil.rmtree(home, ignore_errors=True)
    print("all passed" if not failures else f"failed: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
