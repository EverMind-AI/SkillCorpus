"""Skill Library CLI — click command-line interface.

Usage examples (``--lib`` is a group option and must precede the subcommand):
  python -m skill_library.cli --lib /tmp/lib build
  python -m skill_library.cli --lib /tmp/lib stats
  python -m skill_library.cli --lib /tmp/lib export --out /tmp/corpus
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import click

from .curate.pipeline import SkillLibrary
from .aggregate.fetch import discover_repos, FETCHED as FETCHED_ROOT


_DEFAULT_LIB = str(
    (Path(__file__).resolve().parent / "data").resolve()
)


# ---------------------------------------------------------------------------
# Build pipeline (relocated from scripts/refresh_loop.py; cadence dropped)
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_YAML = Path(__file__).resolve().parent / "sources.yaml"


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8"))


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
        [sys.executable, "-m", "skill_library.aggregate.converters.lobehub",
         "--src", str(src_subdir), "--out", str(out_dir)],
        check=True,
    )
    return _ingest_source(lib, out_dir, source_label)


def _run_module(module: str, argv: list[str]) -> int:
    """Run a curate pass as a subprocess (isolates the heavy LLM / embedding
    passes' memory). Returns the exit code; a non-zero pass does not abort the
    build — a source may legitimately have nothing to score / no whitelist."""
    return subprocess.run(
        [sys.executable, "-m", module, *argv], check=False
    ).returncode


def _post_actions(lib: SkillLibrary, defaults: dict, dry: bool) -> None:
    """The fixed curate -> export tail of the build pipeline, run once after all
    sources are ingested:

        quality_pass -> dedup_pass -> license_audit(activate) -> export.corpus

    These used to be opt-in ops scripts (gated on config flags), which is why an
    un-flagged build could exit 0 having exported nothing. They are unconditional
    steps now, so ``cli build`` always ends by writing the corpus.
    """
    lib_root = str(lib.lib_root)
    db_path = str(lib.store.db_path)
    corpus_out = str(defaults.get("corpus_out") or (lib.lib_root / "corpus"))
    if dry:
        print(f"[dry-run] would run quality_pass -> dedup_pass -> license_audit "
              f"-> export.corpus (db={db_path}, out={corpus_out})", flush=True)
        return

    print("\n→ quality_pass (LLM 3-dim backfill)...", flush=True)
    _run_module("skill_library.curate.quality_pass", ["--lib", lib_root, "--workers", "16"])

    print("\n→ dedup_pass (cross-source near-dup merge)...", flush=True)
    _run_module("skill_library.curate.dedup_pass", ["--lib", lib_root])

    print("\n→ license_audit activate (GREEN whitelist → active)...", flush=True)
    _run_module("skill_library.curate.license_audit", ["activate", "--db", db_path])

    print("\n→ export.corpus (parquet + attachments + card)...", flush=True)
    from skill_library.export.corpus import write_corpus
    stats = write_corpus(db_path, lib_root, corpus_out)
    print(f"  corpus: {stats['rows']} rows, {stats['with_attachments']} "
          f"with attachments → {stats['out']}", flush=True)


def run_refresh(config=DEFAULT_YAML, source=None, dry_run=False,
                lib_root=None) -> int:
    """One-shot build: read the registry -> per source discover -> clone/pull ->
    ingest -> the fixed curate/export tail (see _post_actions). An empty library
    auto-inits (SkillLibrary.open runs init_schema). Every listed source is
    processed; there is no cadence / incremental refresh state.
    """
    cfg = _load_yaml(Path(config))
    sources = cfg.get("sources", []) or []
    defaults = cfg.get("defaults", {}) or {}
    if source:
        sources = [s for s in sources if s.get("name") == source]
        if not sources:
            print(f"!! no source named {source!r}", file=sys.stderr)
            return 1
    print(f"refresh: {len(sources)} sources (dry-run={dry_run})", flush=True)
    if not sources:
        print("nothing to do.", flush=True)
        return 0

    lib = (SkillLibrary(lib_root) if lib_root else SkillLibrary()).open()
    pre_total = lib.stats()["total"]
    summary: list[dict] = []
    # git_clone / lobehub_json are "content-is-itself" sources -> ingest with the
    # entry's source_label; discovery sources expand into many repos, each
    # labelled by its own owner/repo.
    self_types = {"git_clone", "lobehub_json"}
    for s in sources:
        name, typ = s["name"], s["type"]
        print(f"\n=== {name} ({typ}) ===", flush=True)
        try:
            repos = discover_repos(s)
        except Exception as e:
            print(f"  !! discover failed: {e}", flush=True)
            summary.append({"name": name, "error": f"discover: {e}"})
            continue
        print(f"  discover -> {len(repos)} repo", flush=True)
        if dry_run:
            print(f"  [dry-run] would clone/pull + ingest these {len(repos)} repo",
                  flush=True)
            continue

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

    _post_actions(lib, defaults, dry_run)

    post_total = lib.stats()["total"]
    print("\n=== refresh complete ===")
    print(f"total: {pre_total} -> {post_total} (delta {post_total - pre_total})")
    for s in summary:
        print(f"  {s}")
    lib.close()
    return 0


@click.group()
@click.option("--lib", "lib_root", default=_DEFAULT_LIB,
              help=f"Library root dir (default: {_DEFAULT_LIB})")
@click.option("--config", "config_path", default=None, help="Config YAML path")
@click.pass_context
def cli(ctx, lib_root, config_path):
    ctx.ensure_object(dict)
    ctx.obj["lib_root"] = lib_root
    ctx.obj["config_path"] = config_path


@cli.command()
@click.pass_context
def init(ctx):
    """Initialize a skill library directory (path given by --lib)."""
    lib = SkillLibrary(ctx.obj["lib_root"], ctx.obj["config_path"]).open()
    click.echo(f"Initialized skill library at: {lib.lib_root}")
    click.echo(f"  DB: {lib.lib_root / 'index.db'}")
    click.echo(f"  Config: {lib.config_path}")
    click.echo(f"  Embedding available: {lib.embedder.is_available() if lib.embedder else False}")
    lib.close()


@cli.command()
@click.pass_context
def stats(ctx):
    """Library statistics."""
    with SkillLibrary(ctx.obj["lib_root"], ctx.obj["config_path"]) as lib:
        click.echo(json.dumps(lib.stats(), ensure_ascii=False, indent=2))


@cli.command("build")
@click.option("--full", is_flag=True,
              help="use the full registry sources.full.yaml (= shortcut for --sources-config)")
@click.option("--sources-config", "sources_config", type=click.Path(), default=None,
              help="path to the source registry yaml (default: the public demo sources.yaml)")
@click.option("--source", default=None, help="run only the named source")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def build(ctx, full, sources_config, source, dry_run):
    """One-shot pipeline — discover→clone→ingest→quality→dedup→license→corpus, building from scratch.

    An empty library is created automatically (SkillLibrary.open has a built-in init_schema).
    \b
    python -m skill_library.cli build                           # demo build from scratch
    python -m skill_library.cli build --full                    # full registry (production)
    python -m skill_library.cli build --sources-config X.yaml    # bring your own registry
    """
    # registry selection: explicit --sources-config > --full shortcut > default demo
    if sources_config:
        config = Path(sources_config)
    elif full:
        config = Path(__file__).resolve().parent / "sources.full.yaml"
    else:
        config = DEFAULT_YAML
    if not config.exists():
        if sources_config:
            hint = f"registry file does not exist: {config}"
        else:  # from --full
            hint = ("the full registry sources.full.yaml is not shipped with the public release; "
                    "use the default demo in the public version (drop --full), or point --sources-config at your own yaml")
        click.echo(f"ERROR: {hint}", err=True)
        raise SystemExit(2)
    rc = run_refresh(
        config=config, source=source, dry_run=dry_run,
        lib_root=ctx.obj.get("lib_root"),
    )
    raise SystemExit(rc)


@cli.command()
@click.option("--out", "out_path", required=True, type=click.Path(),
              help="Output corpus directory")
@click.pass_context
def export(ctx, out_path):
    """Export the corpus (parquet + attachments + dataset card) from the current library.

    Same output as the final step of `build`, but without re-running the pipeline —
    re-exports whatever is already in the library. Only GREEN-licensed, non-deleted
    skills are written (see docs/corpus-schema.md).
    """
    from .export.corpus import write_corpus
    with SkillLibrary(ctx.obj["lib_root"], ctx.obj["config_path"]) as lib:
        stats = write_corpus(lib.store.db_path, lib.lib_root, out_path)
    click.echo(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    cli(obj={})
