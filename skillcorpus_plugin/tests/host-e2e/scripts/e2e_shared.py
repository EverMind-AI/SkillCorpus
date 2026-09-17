"""The cross-agent acceptance list, on two real hosts.

Everything else about the shared library is unit-tested in-process, which
proves the mechanism and cannot prove the claim. The claim is about two
different agents on one machine seeing each other's skills, and there is no
way to observe that without running two of them.

So: one Python host (Raven) and one TypeScript host (OpenClaw), sharing one
`SKILLSEARCH_HOME`, driven against a real model. Two hosts rather than five
because this is what the two *ports* are — a disagreement between them is the
failure mode that a single-host run cannot see, and a third host of either
kind exercises the same code again.

Covers, from the spec's own list:

    3  a skill agent A **installed from the catalogue** is retrievable in
       agent B — the chain, not either half of it
    4  a skill in agent A's own directory is retrievable in agent B
    5  a skill dropped into the shared directory by hand reaches both,
       with neither restarted
    6  `enabled: false` hides A's directory from B, and survives A restarting
    7  uninstalling in B stops A retrieving it, and leaves a record
    9  a corrupt registry costs sharing and not retrieval

Acceptance 8 is not here: it is a property of the swap primitive, not of two
hosts, and `engine-python/tests` asserts it directly.

Usage:

    export SKILLSEARCH_E2E_BASE_URL=... SKILLSEARCH_E2E_MODEL=...
    python e2e_shared.py --raven /path/to/raven --raven-site /path/to/site-packages \\
                         --openclaw /path/to/node_modules/.bin/openclaw
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _e2e
import e2e_openclaw

RUN_TIMEOUT_S = 600.0

#: A second skill, so a host can be shown finding *the other one's* rather
#: than merely finding something. Facts nowhere else, same rule as `_e2e`.
RAVEN_SKILL = """\
---
name: invoice-audit
description: Audit a batch of supplier invoices for duplicate and out-of-policy charges.
---

House rule: reconcile against the `Wombat-Ledger-7` register and flag anything
above the `Tapir Threshold` for a second reviewer.
"""
RAVEN_FACTS = ("Wombat-Ledger-7", "Tapir Threshold")
RAVEN_PROMPT = "What is our internal procedure for auditing supplier invoices?"

#: The hand-dropped skill, on a subject nothing else here touches.
#:
#: It used to reuse the standard `pdf-tables` fixture, which stopped working
#: the moment this script also installed a real catalogue skill: both are about
#: extracting tables from PDFs, the hosts run with `topK: 1`, and the
#: catalogue's outranked the dropped one. The case then failed while the
#: feature worked — the drop had been found, it just lost the only slot.
DROPPED_SKILL = """\
---
name: rotate-signing-keys
description: Rotate the service signing keys and re-issue downstream credentials safely.
---

House procedure: stage the new key under the `Narwhal-KMS-4` alias and keep the
previous one live until the `Quokka Cutover` window closes.
"""
DROPPED_FACTS = ("Narwhal-KMS-4", "Quokka Cutover")
DROPPED_PROMPT = "What is our internal procedure for rotating signing keys?"


def skill_names(root: Path) -> list[str]:
    """The frontmatter name of every skill under `root`.

    Every one, not the first: a retrieval installs whatever the catalogue
    ranked, which is regularly more than one skill, and the hosts run with
    `topK: 1`. Asserting on one particular installed name therefore measures
    which of them the ranker preferred rather than whether the other host can
    see what this one installed.

    The frontmatter name, not the install slug: the block the model sees
    prints the former and for a catalogue skill the two differ. Looks one
    level down too, because a bundle usually wraps the skill in a directory.
    """
    out: list[str] = []
    for entry in sorted(root.iterdir()) if root.is_dir() else ():
        if not entry.is_dir():
            continue
        for candidate in (entry, *sorted(p for p in entry.iterdir() if p.is_dir())):
            head = candidate / "SKILL.md"
            if not head.is_file():
                continue
            for line in head.read_text(encoding="utf-8", errors="replace").splitlines()[:10]:
                if line.startswith("name:"):
                    out.append(line.split(":", 1)[1].strip())
                    break
            break
    return out


def run_openclaw(openclaw: Path, profile: str, prompt: str, env: dict) -> dict:
    """One OpenClaw turn, reading the answer off the host's own transcript."""
    session = str(uuid.uuid4())
    proc = subprocess.run(
        [str(openclaw), "--profile", profile, "agent", "--local",
         "--thinking", "off", "--session-id", session, "--json", "-m", prompt],
        capture_output=True, text=True, timeout=RUN_TIMEOUT_S, check=False, env=env,
    )
    profile_dir = Path(env["HOME"]) / f".openclaw-{profile}"
    turn = e2e_openclaw.read_turn(e2e_openclaw.transcript(profile_dir, session))
    turn["returncode"] = proc.returncode
    turn["stderr"] = proc.stderr[-3000:]
    return turn


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--openclaw", type=Path, default=os.environ.get("SKILLSEARCH_E2E_OPENCLAW"))
    ap.add_argument("--generation", type=int, default=2, choices=(1, 2))
    ap.add_argument("--raven", type=Path, default=os.environ.get("SKILLSEARCH_E2E_RAVEN_CHECKOUT"))
    ap.add_argument("--raven-site", default=os.environ.get("SKILLSEARCH_E2E_RAVEN_SITE"))
    ap.add_argument("--raven-python", default=sys.executable,
                    help="an interpreter that can import Raven and the plugin")
    ap.add_argument("--dump", type=Path, default=None)
    args = ap.parse_args()
    if not args.openclaw or not args.raven:
        ap.error("--openclaw and --raven are both required; this measures two hosts")

    model = _e2e.model_config()
    home = Path(tempfile.mkdtemp(prefix="skillsearch-two-hosts-"))
    shared_home = home / ".evermind-skillsearch"
    (shared_home / "skills").mkdir(parents=True)
    registry = shared_home / "registry.json"

    # Raven's own directory, with a skill only it has.
    raven_ws = home / "raven-project"
    raven_skills = raven_ws / "skills" / "invoice-audit"
    raven_skills.mkdir(parents=True)
    (raven_skills / "SKILL.md").write_text(RAVEN_SKILL, encoding="utf-8")

    # OpenClaw's own directory, with the standard fixture, so each host has
    # something of its own and "found the other's" is unambiguous.
    oc_skills = home / "openclaw-skills"
    _e2e.corpus(home / "openclaw")
    shutil.move(str(home / "openclaw" / "skills"), str(oc_skills))

    env = {**os.environ, "HOME": str(home), "SKILLSEARCH_HOME": str(shared_home)}
    results: dict = {"home": str(home)}
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str) -> None:
        results[name] = {"pass": ok, "detail": detail}
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        print(f"        {detail}")
        if not ok:
            failures.append(name)

    print(f"shared home: {shared_home}")

    # -- Raven registers its directory ------------------------------------
    raven_env = {**env, "PYTHONPATH": args.raven_site}
    probe = subprocess.run(
        [args.raven_python, "-c",
         "import sys, site, json;"
         f"sys.path.insert(0, {str(args.raven)!r});"
         f"site.addsitedir({args.raven_site!r});"
         "from skillsearch import shared;"
         f"shared.register_host('raven', {str(raven_ws / 'skills')!r});"
         "print(json.dumps([e.as_json() for e in shared.read_registry()]))"],
        capture_output=True, text=True, timeout=120, check=False, env=raven_env,
    )
    registered = json.loads(probe.stdout.strip().splitlines()[-1]) if probe.returncode == 0 else []
    check("raven registers its own skills directory",
          any(e["id"] == "raven" for e in registered),
          f"registry.json now holds {[e['id'] for e in registered]}"
          + ("" if probe.returncode == 0 else f"; stderr={probe.stderr[-400:]}"))

    # -- Acceptance 3: Raven installs from the catalogue, OpenClaw sees it -
    #
    # The chain, run end to end rather than assembled from its halves. Raven
    # retrieves against the live hub, which installs into the shared
    # directory; OpenClaw — a different host, a different language port, its
    # own process — then has to find that skill without being told anything.
    # Reports the block it got as well as what landed. Retrieval fails open, so
    # "the catalogue was unreachable this run" and "nothing installed" look
    # identical from the outside — the distinction is between a flaky service
    # and a broken plugin, and only the second is a failure.
    install_probe = subprocess.run(
        [args.raven_python, "-c",
         "import sys, site, json, asyncio;"
         f"sys.path.insert(0, {str(args.raven)!r});"
         f"site.addsitedir({args.raven_site!r});"
         "from skillsearch import provenance, shared;"
         "from skillsearch.config import SearchConfig;"
         "from skillsearch.engine import SkillSearch;"
         "e = SkillSearch(SearchConfig.from_mapping({"
         f"  'skills_dir': {str(raven_ws / 'skills')!r},"
         "  'hub_endpoint': 'https://skillhub.evermind.ai',"
         "  'clawhub_endpoint': '', 'skillhub_cn_endpoint': '',"
         "  'top_k': 2, 'model': '', 'gate': False}));"
         "block = asyncio.run(e.retrieve('extract tables from a PDF'));"
         "print(json.dumps({'block': len(block), 'installed': "
         "[o.as_json() for o in provenance.list_installed(shared.shared_skills_dir())]}))"],
        capture_output=True, text=True, timeout=300, check=False, env=raven_env,
    )
    probe: dict = {}
    if install_probe.returncode == 0 and install_probe.stdout.strip():
        probe = json.loads(install_probe.stdout.strip().splitlines()[-1])
    catalogue = probe.get("installed") or []
    if not catalogue and probe.get("block") == 0:
        # The service answered with nothing, or not at all. Not a verdict on
        # the plugin; say so rather than reporting a red that a rerun clears.
        results["catalogue"] = {"pass": None, "detail": "BLOCKED — the catalogue returned nothing"}
        print("  BLOCKED  the catalogue returned nothing this run; acceptance 3 and 7 skipped")
    else:
        detail = f"{[c['origin'] for c in catalogue]}, retrieval block {probe.get('block')} chars"
        if install_probe.returncode != 0:
            detail += f"; stderr={install_probe.stderr[-300:]}"
        check("raven installs a catalogue skill into the shared directory", bool(catalogue), detail)

    e2e_openclaw.write_profile(
        home / ".openclaw-shared-e2e", args.generation, "on_demand", oc_skills,
        home / "oc-workspace", model,
    )
    installed_names: list[str] = []
    if catalogue:
        installed_names = skill_names(shared_home / "skills")
        turn = run_openclaw(Path(args.openclaw), "shared-e2e",
                            "How do I pull the tables out of a PDF report?", env)
        seen = "\n".join(turn["tool_results"]) + "\n" + turn["reply"]
        found = [name for name in installed_names if name and name in seen]
        check("openclaw retrieves a skill raven installed from the catalogue",
              bool(found),
              f"found {found} of {installed_names}; "
              f"tools={[c['name'] for c in turn['tool_calls']]}")

    # -- Acceptance 4: OpenClaw finds Raven's own hand-written skill -------
    e2e_openclaw.write_profile(
        home / ".openclaw-shared-e2e", args.generation, "on_demand", oc_skills,
        home / "oc-workspace", model,
    )
    turn = run_openclaw(Path(args.openclaw), "shared-e2e", RAVEN_PROMPT, env)
    delivered = "\n".join(turn["tool_results"]) + "\n" + turn["reply"]
    check("openclaw retrieves a skill that lives in raven's directory",
          all(fact in delivered for fact in RAVEN_FACTS),
          f"tools={[c['name'] for c in turn['tool_calls']]} reply={turn['reply'][:160]!r}")

    # -- Acceptance 5: dropped in by hand, no restart ----------------------
    #
    # Its own subject, deliberately. The hosts run with `topK: 1`, so a fixture
    # that competes with the catalogue skill installed above measures which of
    # the two ranks higher rather than whether the drop was noticed.
    dropped = shared_home / "skills" / "rotate-signing-keys"
    dropped.mkdir(parents=True)
    (dropped / "SKILL.md").write_text(DROPPED_SKILL, encoding="utf-8")
    turn = run_openclaw(Path(args.openclaw), "shared-e2e", DROPPED_PROMPT, env)
    delivered = "\n".join(turn["tool_results"]) + "\n" + turn["reply"]
    check("a skill dropped into the shared directory is found without a restart",
          all(fact in delivered for fact in DROPPED_FACTS),
          f"tools={[c['name'] for c in turn['tool_calls']]} reply={turn['reply'][:160]!r}")

    # -- Acceptance 7: uninstall, across hosts -----------------------------
    #
    # Removed through the Python port, observed from the TypeScript host — the
    # direction that matters, since a ledger the other side cannot see is not
    # a shared library.
    if catalogue:
        origin = catalogue[0]["origin"]
        removal = subprocess.run(
            [args.raven_python, "-c",
             "import sys, site, json;"
             f"sys.path.insert(0, {str(args.raven)!r});"
             f"site.addsitedir({args.raven_site!r});"
             "from skillsearch import provenance, shared;"
             "root = shared.shared_skills_dir();"
             f"ok = provenance.uninstall(root, {origin!r});"
             "print(json.dumps({'ok': ok, "
             "'left': [o.origin for o in provenance.list_installed(root)], "
             "'log': [r['origin'] for r in provenance.read_uninstalled(root)]}))"],
            capture_output=True, text=True, timeout=120, check=False, env=raven_env,
        )
        ok_run = removal.returncode == 0 and removal.stdout.strip()
        state = json.loads(removal.stdout.strip().splitlines()[-1]) if ok_run else {}
        check("uninstalling removes it and records that it was removed",
              state.get("ok") is True and origin not in state.get("left", [origin])
              and origin in state.get("log", []),
              f"{state}" + ("" if removal.returncode == 0 else f"; stderr={removal.stderr[-300:]}"))

        # What is gone is what this identity's directory held, which is one of
        # the installed names — the others are still installed and may well be
        # retrieved instead. So the assertion is about the removed one alone.
        removed_names = [n for n in installed_names if n not in skill_names(shared_home / "skills")]
        turn = run_openclaw(Path(args.openclaw), "shared-e2e",
                            "How do I pull the tables out of a PDF report?", env)
        seen = "\n".join(turn["tool_results"]) + "\n" + turn["reply"]
        still = [n for n in removed_names if n in seen]
        check("and the other host stops retrieving it, without a restart",
              bool(removed_names) and not still,
              f"removed {removed_names}; still visible {still}")

    # -- Acceptance 6: enabled:false hides it, and survives a restart ------
    document = json.loads(registry.read_text(encoding="utf-8"))
    for entry in document["hosts"]:
        if entry["id"] == "raven":
            entry["enabled"] = False
    registry.write_text(json.dumps(document, indent=2), encoding="utf-8")

    turn = run_openclaw(Path(args.openclaw), "shared-e2e", RAVEN_PROMPT, env)
    delivered = "\n".join(turn["tool_results"]) + "\n" + turn["reply"]
    check("disabling raven's entry hides its skills from openclaw",
          not any(fact in delivered for fact in RAVEN_FACTS),
          f"reply={turn['reply'][:160]!r}")

    subprocess.run(
        [args.raven_python, "-c",
         "import sys, site;"
         f"sys.path.insert(0, {str(args.raven)!r});"
         f"site.addsitedir({args.raven_site!r});"
         "from skillsearch import shared;"
         f"shared.register_host('raven', {str(raven_ws / 'skills')!r})"],
        capture_output=True, text=True, timeout=120, check=False, env=raven_env,
    )
    after = json.loads(registry.read_text(encoding="utf-8"))
    still_off = [e for e in after["hosts"] if e["id"] == "raven" and e["enabled"] is False]
    check("and raven restarting does not switch it back on",
          bool(still_off),
          f"registry after re-registration: {[(e['id'], e['enabled']) for e in after['hosts']]}")

    # -- Acceptance 9: a corrupt registry costs sharing, not retrieval -----
    registry.write_text("{ this is not json", encoding="utf-8")
    turn = run_openclaw(Path(args.openclaw), "shared-e2e", _e2e.PROMPT_POSITIVE, env)
    check("a corrupt registry leaves retrieval working",
          turn["returncode"] == 0 and bool(turn["reply"]),
          f"rc={turn['returncode']} reply={turn['reply'][:160]!r}")

    if args.dump:
        args.dump.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print("all passed" if not failures else f"failed: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
