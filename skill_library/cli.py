"""Skill Library CLI — click command-line interface.

Usage examples (``--lib`` is a group option and must precede the subcommand):
  python -m skill_library.cli --lib /tmp/lib init
  python -m skill_library.cli --lib /tmp/lib add /path/to/pdf --source anthropics
  python -m skill_library.cli --lib /tmp/lib add-batch /path/to/skills --source anthropics
  python -m skill_library.cli --lib /tmp/lib stats
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from .curate.pipeline import SkillLibrary


def _fmt_record(r) -> dict:
    return {
        "skill_id": r.skill_id,
        "name": r.name,
        "description": r.description[:100] + ("..." if len(r.description) > 100 else ""),
        "source": r.source,
        "category": r.category,
        "tags": r.tags,
        "quality": r.quality_score,
        "safety_flags": r.safety_flags,
        "has_scripts": r.has_scripts,
        "body_tokens": r.body_tokens,
    }


_DEFAULT_LIB = str(
    (Path(__file__).resolve().parent / "data").resolve()
)


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
@click.argument("skill_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--source", required=True, help="Source tag (anthropics/karanb192/...)")
@click.option("--source-url", default=None)
@click.option("--force", is_flag=True, help="Force re-ingest even if duplicate")
@click.pass_context
def add(ctx, skill_dir, source, source_url, force):
    """Add a single skill directory to the library."""
    with SkillLibrary(ctx.obj["lib_root"], ctx.obj["config_path"]) as lib:
        result = lib.add(skill_dir, source=source, source_url=source_url, force=force)
        click.echo(f"Status: {result.status.value}")
        if result.reason:
            click.echo(f"  Reason: {result.reason}")
        if result.record:
            click.echo(json.dumps(_fmt_record(result.record), ensure_ascii=False, indent=2))


@cli.command("add-batch")
@click.argument("root", type=click.Path(exists=True, file_okay=False))
@click.option("--source", required=True)
@click.option("--pattern", default="**/SKILL.md")
@click.option("--limit", type=int, default=None)
@click.pass_context
def add_batch(ctx, root, source, pattern, limit):
    """Batch-scan a directory and ingest the dir containing each SKILL.md."""
    with SkillLibrary(ctx.obj["lib_root"], ctx.obj["config_path"]) as lib:
        out = lib.add_batch(root, source=source, pattern=pattern, limit=limit)
        samples = out.pop("rejected_samples", [])
        added_ids = out.pop("added_ids", [])
        click.echo(json.dumps(out, ensure_ascii=False, indent=2))
        if samples:
            click.echo("\nRejected samples (first 10):")
            for path, reason in samples[:10]:
                click.echo(f"  - {path}: {reason}")


# NOTE: this CLI only covers ingestion / build / ops; runtime retrieval (BM25+embedding
# search over the library) is the consumer's responsibility, so no `search` subcommand is provided here.


@cli.command()
@click.argument("skill_id")
@click.pass_context
def get(ctx, skill_id):
    """Show the details of a single skill."""
    with SkillLibrary(ctx.obj["lib_root"], ctx.obj["config_path"]) as lib:
        r = lib.get(skill_id)
        if r is None:
            click.echo(f"not found: {skill_id}", err=True)
            sys.exit(1)
        d = _fmt_record(r)
        d["body_preview"] = r.body[:500] + ("..." if len(r.body) > 500 else "")
        d["stored_path"] = r.stored_path
        click.echo(json.dumps(d, ensure_ascii=False, indent=2))


@cli.command("list")
@click.option("--category", default=None)
@click.option("--source", default=None)
@click.option("--tag", default=None)
@click.option("--min-quality", default=0.0, type=float)
@click.option("--limit", default=50, type=int)
@click.pass_context
def list_cmd(ctx, category, source, tag, min_quality, limit):
    """List skills (optionally filtered by category/source/tag/quality)."""
    with SkillLibrary(ctx.obj["lib_root"], ctx.obj["config_path"]) as lib:
        records = lib.list(
            category=category, source=source, tag=tag,
            min_quality=min_quality, limit=limit,
        )
        click.echo(f"{len(records)} records:")
        for r in records:
            click.echo(f"  [{r.category:14s}] {r.skill_id}  (q={r.quality_score})")
            click.echo(f"                   {r.description[:100]}")


@cli.command()
@click.argument("skill_id")
@click.option("--hard", is_flag=True, help="Physical delete (also remove files)")
@click.pass_context
def delete(ctx, skill_id, hard):
    """Delete a skill (soft by default)."""
    with SkillLibrary(ctx.obj["lib_root"], ctx.obj["config_path"]) as lib:
        ok = lib.delete(skill_id, soft=not hard)
        click.echo("deleted" if ok else "not found")
        sys.exit(0 if ok else 1)


@cli.command()
@click.pass_context
def stats(ctx):
    """Library statistics."""
    with SkillLibrary(ctx.obj["lib_root"], ctx.obj["config_path"]) as lib:
        click.echo(json.dumps(lib.stats(), ensure_ascii=False, indent=2))


@cli.command("build")
@click.option("--update", is_flag=True,
              help="incremental mode: run only the sources due per cadence (default runs everything from scratch)")
@click.option("--full", is_flag=True,
              help="use the full registry sources.full.yaml (= shortcut for --sources-config)")
@click.option("--sources-config", "sources_config", type=click.Path(), default=None,
              help="path to the source registry yaml (default: the public demo sources.yaml)")
@click.option("--source", default=None, help="run only the named source")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def build(ctx, update, full, sources_config, source, dry_run):
    """One-shot full pipeline — discover→clone→ingest→quality→export, building from scratch / incremental update.

    An empty library is created automatically (SkillLibrary.open has a built-in init_schema). This is the facade for refresh_loop.
    \b
    python -m skill_library.cli build                       # demo build from scratch
    python -m skill_library.cli build --update              # incremental (per cadence)
    python -m skill_library.cli build --full                # full registry (production)
    python -m skill_library.cli build --sources-config X.yaml  # bring your own registry
    """
    from pathlib import Path
    from .scripts.refresh_loop import run_refresh, DEFAULT_YAML
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
        config=config, source=source, force=not update, dry_run=dry_run,
        lib_root=ctx.obj.get("lib_root"),
    )
    raise SystemExit(rc)


@cli.command()
@click.argument("skill_id")
@click.argument("tags")
@click.pass_context
def retag(ctx, skill_id, tags):
    """Update the tag list (comma-separated)."""
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    with SkillLibrary(ctx.obj["lib_root"], ctx.obj["config_path"]) as lib:
        r = lib.retag(skill_id, tag_list)
        if r is None:
            click.echo("not found", err=True)
            sys.exit(1)
        click.echo(f"tags updated: {r.tags}")


@cli.command()
@click.argument("skill_id")
@click.argument("category")
@click.pass_context
def reclassify(ctx, skill_id, category):
    """Change the primary category."""
    with SkillLibrary(ctx.obj["lib_root"], ctx.obj["config_path"]) as lib:
        r = lib.reclassify(skill_id, category)
        if r is None:
            click.echo("not found", err=True)
            sys.exit(1)
        click.echo(f"category updated: {r.category}")


@cli.command()
@click.option("--out", "out_path", required=True, type=click.Path(),
              help="Output zip path")
@click.option("--ids", default=None, help="Comma-separated skill_ids")
@click.option("--category", default=None)
@click.option("--source", default=None)
@click.option("--tag", default=None)
@click.option("--min-quality", default=0.0, type=float)
@click.option("--limit", default=10_000, type=int)
@click.pass_context
def export(ctx, out_path, ids, category, source, tag, min_quality, limit):
    """Export a subset of skills as a zip (containing manifest.json + skill directories)."""
    skill_ids = [s.strip() for s in ids.split(",") if s.strip()] if ids else None
    with SkillLibrary(ctx.obj["lib_root"], ctx.obj["config_path"]) as lib:
        out = lib.export_bundle(
            out_path=out_path, skill_ids=skill_ids,
            category=category, source=source, tag=tag,
            min_quality=min_quality, limit=limit,
        )
        click.echo(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    cli(obj={})
