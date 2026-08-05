"""Skill Library CLI — click command-line interface.

Usage examples (``--lib`` is a group option and must precede the subcommand):
  python -m skill_library.cli --lib /tmp/lib build
  python -m skill_library.cli --lib /tmp/lib stats
  python -m skill_library.cli --lib /tmp/lib export --out /tmp/corpus
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from .curate.pipeline import SkillLibrary


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
    """One-shot full pipeline — discover→clone→ingest→quality→dedup→license→corpus, building from scratch / incremental update.

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
