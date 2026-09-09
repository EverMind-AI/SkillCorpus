"""Does *each* host actually join the shared library?

`e2e_shared.py` proves two hosts share with each other. This asks the narrower
question of every host separately, because the answer is per-host code: the
self-registration is wired at six separate call sites, each with its own config
key and its own place in that host's engine builder. Three "fixed on one side
only" bugs have already come out of this branch, and every one of them was
invisible from inside the other side.

The setup is the same for all of them, so a difference in the result is a
difference in the host's wiring and nothing else:

- one skill, in the shared directory, on a subject nothing else here touches
  and carrying facts that exist nowhere outside this repository;
- the host's own skills directory created and left **empty** — a host that
  configures no directory deliberately does not join the shared library, so an
  empty one isolates "does this host read the others" with nothing of its own
  to find instead;
- one question, whose answer is only in that skill.

A host passes when its own line appears in `registry.json` **and** the shared
skill reaches the model.

Those are two questions and the second one is not entirely the plugin's to
answer: in on-demand mode the model decides whether to call `skill_search` at
all, and a model that answers from memory instead leaves the wiring untested
rather than broken. Measured: OpenClaw 1.x failed this exactly once that way,
and driving its engine directly returned the shared skill in full.

So a host that registers but whose model did not reach for the tool is
reported as INCONCLUSIVE, not FAIL, and the run says which. A host that does
not register is a real failure — it means the others cannot see it, which is
half the feature gone.

Usage:

    export SKILLSEARCH_E2E_BASE_URL=... SKILLSEARCH_E2E_MODEL=...
    python e2e_shared_hosts.py --raven ... --hermes ... --dsh ... \\
                               --openclaw1 ... --openclaw2 ...

Every host is optional; the ones not given are reported as skipped rather than
silently dropped. WorkBuddy has no headless path and is never run here.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _e2e
import e2e_openclaw

TIMEOUT_S = 600.0
ENGINE_PY = Path(__file__).resolve().parents[3] / "engine-python"


def registered(home: Path) -> list[str]:
    """Host ids in the shared registry, read without importing the engine."""
    try:
        raw = json.loads((home / ".evermind-skillsearch" / "registry.json").read_text("utf-8"))
    except (OSError, ValueError):
        return []
    return [str(h.get("id")) for h in raw.get("hosts") or [] if isinstance(h, dict)]


def python_host(kind: str, checkout: Path, site: str, python: str, home: Path,
                own: Path, model: dict) -> dict:
    """Drive Raven or Hermes in a subprocess with the shared home in place.

    A subprocess because `SKILLSEARCH_HOME` and the host's own import side
    effects have to be this run's, not this interpreter's.
    """
    driver = Path(__file__).resolve().parent / f"e2e_{kind}.py"
    env = {
        **os.environ,
        "HOME": str(home),
        "SKILLSEARCH_HOME": str(home / ".evermind-skillsearch"),
        "SKILLSEARCH_E2E_SHARED_OWN_DIR": str(own),
        "SKILLSEARCH_E2E_SHARED_PROMPT": _e2e.SHARED_PROMPT,
    }
    extra = ["--plugin-site", site] if kind == "raven" else ["--prefetch-budget", "300"]
    proc = subprocess.run(
        [python, str(driver), "--host", str(checkout), *extra, "--shared-probe", "on_demand"],
        capture_output=True, text=True, timeout=TIMEOUT_S + 120, check=False, env=env,
    )
    return {"returncode": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-2000:]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raven", type=Path)
    ap.add_argument("--raven-site")
    ap.add_argument("--raven-python", default=sys.executable)
    ap.add_argument("--hermes", type=Path)
    ap.add_argument("--hermes-python", default=sys.executable)
    ap.add_argument("--dsh", type=Path)
    ap.add_argument("--openclaw1", type=Path)
    ap.add_argument("--openclaw2", type=Path)
    ap.add_argument("--dump", type=Path, default=None)
    args = ap.parse_args()

    model = _e2e.model_config()
    results: dict = {}
    failures: list[str] = []
    skipped: list[str] = []

    inconclusive: list[str] = []

    def check(host: str, registered_ok: bool, retrieved: bool, called: bool, detail: str) -> None:
        """Two questions, reported apart.

        Registration is the plugin's alone, so its failure is this host's
        failure. Retrieval in on-demand mode goes through the model's choice
        to call the tool, so "did not retrieve because it never asked" is not
        evidence against the wiring.
        """
        if registered_ok and retrieved:
            verdict, bucket = "PASS", None
        elif not registered_ok:
            verdict, bucket = "FAIL", failures
        elif not called:
            verdict, bucket = "INCONCLUSIVE", inconclusive
        else:
            verdict, bucket = "FAIL", failures
        results[host] = {"verdict": verdict, "registered": registered_ok,
                         "retrieved": retrieved, "called_tool": called, "detail": detail}
        print(f"  {verdict:13} {host}")
        print(f"        {detail}")
        if bucket is not None:
            bucket.append(host)

    # -- the two OpenClaw generations -------------------------------------
    # `label` is what this script calls the host; `host_id` is what the plugin
    # registers itself as. They differ for 1.x — the package predates the 2.0
    # split and still registers as `openclaw` — and comparing the wrong one
    # reported a host that had registered correctly as a failure.
    for label, host_id, executable, generation in (("openclaw1", "openclaw", args.openclaw1, 1),
                                                   ("openclaw2", "openclaw2", args.openclaw2, 2)):
        if not executable:
            skipped.append(label)
            continue
        home = Path(tempfile.mkdtemp(prefix=f"shared-{label}-"))
        own, _ = _e2e.shared_corpus(home)
        profile = f"shared-{label}"
        e2e_openclaw.write_profile(home / f".openclaw-{profile}", generation, "on_demand",
                                   own, home / "workspace", model)
        env = {**os.environ, "HOME": str(home),
               "SKILLSEARCH_HOME": str(home / ".evermind-skillsearch")}
        session = str(uuid.uuid4())
        proc = subprocess.run(
            [str(executable), "--profile", profile, "agent", "--local", "--thinking", "off",
             "--session-id", session, "--json", "-m", _e2e.SHARED_PROMPT],
            capture_output=True, text=True, timeout=TIMEOUT_S, check=False, env=env,
        )
        turn = e2e_openclaw.read_turn(
            e2e_openclaw.transcript(home / f".openclaw-{profile}", session))
        seen = "\n".join(turn["tool_results"]) + "\n" + turn["reply"]
        ids = registered(home)
        names = [c["name"] for c in turn["tool_calls"]]
        check(label,
              host_id in ids,
              all(fact in seen for fact in _e2e.SHARED_FACTS),
              "skill_search" in names,
              f"registry={ids} tools={names} reply={turn['reply'][:140]!r}"
              + ("" if proc.returncode == 0 else f" stderr={proc.stderr[-300:]}"))

    # -- the DeepSeek Harness ---------------------------------------------
    if args.dsh:
        home = Path(tempfile.mkdtemp(prefix="shared-dsh-"))
        own, _ = _e2e.shared_corpus(home)
        driver = Path(__file__).resolve().parent / "e2e_deepseek.py"
        env = {**os.environ, "HOME": str(home),
               "SKILLSEARCH_HOME": str(home / ".evermind-skillsearch"),
               "SKILLSEARCH_E2E_SHARED_OWN_DIR": str(own),
               "SKILLSEARCH_E2E_SHARED_PROMPT": _e2e.SHARED_PROMPT}
        proc = subprocess.run(
            [sys.executable, str(driver), "--host", str(args.dsh), "--shared-probe", "on_demand"],
            capture_output=True, text=True, timeout=TIMEOUT_S + 120, check=False, env=env,
        )
        ids = registered(home)
        check("deepseek-harness",
              "deepseek-harness" in ids,
              "SHARED-PROBE PASS" in proc.stdout,
              "skill_search" in proc.stdout,
              f"registry={ids} " + (proc.stdout[-300:] or proc.stderr[-300:]))
    else:
        skipped.append("deepseek-harness")

    # -- Raven and Hermes --------------------------------------------------
    for label, checkout, site, python in (
        ("raven", args.raven, args.raven_site, args.raven_python),
        ("hermes", args.hermes, "", args.hermes_python),
    ):
        if not checkout:
            skipped.append(label)
            continue
        home = Path(tempfile.mkdtemp(prefix=f"shared-{label}-"))
        own, _ = _e2e.shared_corpus(home)
        out = python_host(label, Path(checkout), site or "", python, home, own, model)
        ids = registered(home)
        check(label,
              label in ids,
              "SHARED-PROBE PASS" in out["stdout"],
              "skill_search" in out["stdout"],
              f"registry={ids} " + (out["stdout"][-300:] or out["stderr"][-300:]))

    if skipped:
        print(f"\n  skipped (not given): {', '.join(skipped)}")
    print("  workbuddy: no headless path; verified by hand — see reports/")

    if args.dump:
        args.dump.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    if inconclusive:
        print(f"\n  inconclusive (registered, but the model never called the tool): "
              f"{', '.join(inconclusive)}")
        print("  rerun those, or drive their engine directly — the wiring is not what "
              "this measured")
    print("\nall passed" if not failures else f"\nfailed: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
