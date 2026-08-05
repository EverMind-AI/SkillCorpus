"""V5-1: scheduled refresh of large/active skill sources.

Reads ``skill_library/sources.yaml`` and for each source whose
``pull_cadence`` interval has elapsed since the last run:

    1. git pull (or clone if first time) into ``_fetched/<owner>/<repo>/``
    2. lib.add_batch with fast-batch strategy
       (LLM classify/quality OFF inline, rescan_quality post)
    3. rescan_quality on newly-added skills (LLM score backfill)
    4. export_to_mass_library (writes Ever-v2 mass_library.db + .stale flag)

State persisted in ``refresh_state.json`` under ``data/`` so consecutive
runs only touch sources whose cadence has elapsed.

Usage:
    python -m skill_library.scripts.refresh_loop                # auto
    python -m skill_library.scripts.refresh_loop --tier daily   # only daily
    python -m skill_library.scripts.refresh_loop --source openclaw/skills
    python -m skill_library.scripts.refresh_loop --force        # ignore cadence
    python -m skill_library.scripts.refresh_loop --dry-run

Cron suggestion:
    0 3 * * *   python -m skill_library.scripts.refresh_loop
        # picks up daily Tier-1 each night, weekly Tier-2 every 7d, etc.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from skill_library import SkillLibrary  # noqa: E402
from skill_library.aggregate.fetch import discover_repos  # noqa: E402

FETCHED_ROOT = REPO_ROOT / "experiment-results" / "_reference_skills" / "_fetched"
STATE_FILE = REPO_ROOT / "skill_library" / "data" / "refresh_state.json"
DEFAULT_YAML = REPO_ROOT / "skill_library" / "sources.yaml"

CADENCE_TO_DELTA = {
    "daily": timedelta(hours=20),    # 20h leeway so a daily cron lands every run
    "weekly": timedelta(days=6),     # similar leeway
    "monthly": timedelta(days=28),
    "manual": timedelta(days=10**6),  # never auto, only via --source / --force
}


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _is_due(name: str, cadence: str, state: dict, force: bool = False) -> bool:
    if force or cadence == "manual":
        return force  # only force enables manual
    last = state.get(name, {}).get("last_success")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except ValueError:
        return True
    return datetime.now(timezone.utc) - last_dt >= CADENCE_TO_DELTA.get(
        cadence, CADENCE_TO_DELTA["weekly"]
    )


def _git_pull_or_clone(repo: str, timeout: int = 300) -> tuple[Path | None, str]:
    """Returns (path, status) where status ∈ {cloned, pulled, no_change, failed}."""
    import os as _os
    owner, name = repo.split("/", 1)
    dst = FETCHED_ROOT / owner / name
    env = {**_os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true"}

    if (dst / ".git").is_dir():
        # pull
        try:
            r = subprocess.run(
                ["git", "-C", str(dst), "pull", "--quiet", "--rebase=false"],
                timeout=timeout, capture_output=True, env=env, check=True,
            )
            return dst, "pulled"
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            err = (e.stderr or b"").decode("utf-8", "replace")[:200] if hasattr(e, "stderr") else str(e)
            return None, f"failed: {err}"
    else:
        # clone
        dst.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://github.com/{repo}.git"
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "--quiet", url, str(dst)],
                timeout=timeout, capture_output=True, env=env, check=True,
            )
            return dst, "cloned"
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            if dst.exists():
                shutil.rmtree(dst, ignore_errors=True)
            err = (e.stderr or b"").decode("utf-8", "replace")[:200] if hasattr(e, "stderr") else str(e)
            return None, f"failed: {err}"


def _ingest_source(lib: SkillLibrary, src_dir: Path, source_label: str) -> dict:
    """Run fast-batch ingest.

    Only the expensive LLM quality judge is disabled inline (it is backfilled
    by rescan_quality in ``_post_actions``). The classifier stays enabled: one
    classify call per skill is cheap relative to the quality judge, and without
    it every skill's category would be OTHER. When no LLM is reachable the
    ingester already leaves ``classifier=None`` (see ``SkillLibrary.open``), so
    keeping it here never adds a per-skill timeout.
    """
    if lib.ingester is not None:
        lib.ingester.quality_judge = None
    return lib.add_batch(src_dir, source=source_label)


def _ingest_lobehub(lib: SkillLibrary, src_dir: Path, source_label: str) -> dict:
    """Special path: run lobehub_to_skills converter then add_batch."""
    out_dir = Path("/tmp") / f"lobehub-converted-{int(time.time())}"
    src_subdir = src_dir / "src"
    if not src_subdir.is_dir():
        return {"total": 0, "added": 0, "error": f"missing src/ in {src_dir}"}
    subprocess.run(
        [sys.executable, "-m", "skill_library.scripts.lobehub_to_skills",
         "--src", str(src_subdir), "--out", str(out_dir)],
        check=True,
    )
    return _ingest_source(lib, out_dir, source_label)


def _post_actions(lib: SkillLibrary, defaults: dict, dry: bool) -> None:
    """Run rescan_quality + sync after refresh batch is done."""
    if dry:
        print("[dry-run] would rescan_quality + export_to_mass_library", flush=True)
        return
    # The post-action subprocesses default to the package data dir, so a custom
    # run_refresh(lib_root=...) would otherwise rescan / export the WRONG
    # library. Thread the actual lib_root through as --lib / --src.
    lib_root = str(lib.lib_root)
    if defaults.get("rescan_quality_after"):
        print("\n→ rescan_quality (LLM backfill on new skills)...", flush=True)
        subprocess.run(
            [sys.executable, "-m", "skill_library.curate.quality_pass",
             "--lib", lib_root, "--workers", "16"],
            check=False,
        )
    if defaults.get("export_after"):
        print("\n→ export_to_mass_library (Ever-v2 mass_library.db + .stale flag)...",
              flush=True)
        export_cmd = [
            sys.executable, "-m", "skill_library.export",
            "--src", lib_root,
        ]
        # Optional refresh-endpoint pass-through so the produced sentinel
        # tells downstream consumers where to call ``skill refresh``.
        refresh_endpoint = defaults.get("export_refresh_endpoint")
        if refresh_endpoint:
            export_cmd += ["--refresh-endpoint", str(refresh_endpoint)]
        dst = defaults.get("export_dst")
        if dst:
            export_cmd += ["--dst", str(dst)]
        subprocess.run(export_cmd, check=False)


def run_refresh(
    config: Path = DEFAULT_YAML,
    source: str | None = None,
    tier: str | None = None,
    force: bool = False,
    dry_run: bool = False,
    lib_root: str | Path | None = None,
) -> int:
    """One-shot full pipeline: read the registry → discover per source → clone/pull → ingest →
    rescan_quality → export. An empty library auto-inits (SkillLibrary.open has init_schema built in).

    force=True → ignore cadence and run everything (for building a library from scratch);
    force=False → incremental by cadence.
    Shared by ``main()`` (CLI argv) and the ``cli.py:build`` subcommand.
    """
    cfg = _load_yaml(Path(config))
    sources = cfg.get("sources", []) or []
    defaults = cfg.get("defaults", {}) or {}
    state = _load_state()

    # Filter
    if source:
        sources = [s for s in sources if s.get("name") == source]
        if not sources:
            print(f"!! no source named {source!r}", file=sys.stderr)
            return 1
    if tier:
        sources = [s for s in sources if s.get("pull_cadence") == tier]

    due = [s for s in sources
           if _is_due(s["name"], s.get("pull_cadence", "weekly"),
                      state, force=force)]
    print(f"refresh: {len(due)}/{len(sources)} sources due "
          f"(force={force}, dry-run={dry_run})", flush=True)

    if not due:
        print("nothing to do.", flush=True)
        return 0

    lib = (SkillLibrary(lib_root) if lib_root else SkillLibrary()).open()
    pre_total = lib.stats()["total"]

    summary: list[dict] = []
    # git_clone / lobehub_json are "content-is-itself" sources → ingest uses the entry's source_label;
    # readme_scrape / index_api / sitemap_scrape / json_catalog are "discovery" sources →
    # expanded into multiple repos, each repo using its own owner/repo as the source label (consistent
    # with full crawl-ingest behavior, where the DB records source by owner/repo).
    self_types = {"git_clone", "lobehub_json"}
    for s in due:
        name, typ = s["name"], s["type"]
        print(f"\n=== {name} ({s.get('pull_cadence')}, {typ}) ===", flush=True)

        # discover: which repos this source should ingest (itself or expanded)
        try:
            repos = discover_repos(s)
        except Exception as e:
            print(f"  !! discover failed: {e}", flush=True)
            summary.append({"name": name, "error": f"discover: {e}"})
            continue
        print(f"  discover → {len(repos)} repo", flush=True)

        if dry_run:
            print(f"  [dry-run] would clone/pull + ingest these {len(repos)} repo",
                  flush=True)
            continue

        # clone/pull + ingest each discovered repo
        added_total = 0
        ok = 0
        for owner, repo in repos:
            src_dir, git_status = _git_pull_or_clone(f"{owner}/{repo}")
            if src_dir is None:
                continue
            label = (s.get("source_label") or s["repo"]) if typ in self_types else f"{owner}/{repo}"
            try:
                if typ == "lobehub_json":
                    result = _ingest_lobehub(lib, src_dir, label)
                else:
                    result = _ingest_source(lib, src_dir, label)
                added_total += result.get("added", 0)
                ok += 1
            except Exception as e:
                print(f"    !! ingest {owner}/{repo} failed: {e}", flush=True)
        print(f"  ingested {ok}/{len(repos)} repo, added={added_total}", flush=True)
        summary.append({"name": name, "repos": len(repos),
                        "ingested": ok, "added": added_total})

        # mark last_success only on "progress": at least one ingest succeeded, or there were no
        # repos to do. If repos were discovered but all clone/ingest failed (ok==0), don't mark
        # success → retry on the next cadence, avoiding a bad source being skipped forever.
        if ok > 0 or not repos:
            state.setdefault(name, {})["last_success"] = (
                datetime.now(timezone.utc).isoformat()
            )
            state[name]["last_added"] = added_total
        else:
            print(f"  !! {name}: discovered {len(repos)} repo but all failed, not marking success (retry next time)",
                  flush=True)
        _save_state(state)

    # 4. post actions (rescan + sync), once after all sources
    _post_actions(lib, defaults, dry_run)

    post_total = lib.stats()["total"]
    print(f"\n=== refresh complete ===")
    print(f"total: {pre_total} → {post_total} (Δ {post_total-pre_total})")
    for s in summary:
        print(f"  {s}")

    lib.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=DEFAULT_YAML)
    ap.add_argument("--source", help="only refresh this source name")
    ap.add_argument("--tier", choices=["daily", "weekly", "monthly"],
                    help="only refresh sources with this cadence")
    ap.add_argument("--force", action="store_true",
                    help="ignore cadence, refresh all selected sources")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return run_refresh(config=args.config, source=args.source, tier=args.tier,
                       force=args.force, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
