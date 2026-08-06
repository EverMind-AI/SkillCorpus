"""Skill Library CLI — click command-line interface.

Usage examples (``--lib`` is a group option and must precede the subcommand):
  python -m skillcorpus.cli --lib /tmp/lib build
  python -m skillcorpus.cli --lib /tmp/lib stats
  python -m skillcorpus.cli --lib /tmp/lib export --out /tmp/corpus
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import click

import logging
from .core.store import SkillStore
from .core.embed import EmbeddingClient
from .core.llm import LLMClient
from .core.models import SkillRecord
from .core.config import load_config
from .curate.classify import Classifier
from .curate.dedup import LLMDupJudge
from .curate.quality import LLMQualityJudge
from .curate.pipeline import Ingester, IngestResult
from .core.paths import SKILLCORPUS_HOME
from .aggregate.discover import discover_repos
from .aggregate.clone import clone_or_pull
from .aggregate.registry import load_registry


_DEFAULT_LIB = str(SKILLCORPUS_HOME)

logger = logging.getLogger("skillcorpus.cli")


class SkillLibrary:
    """Skill library top-level API — CRUD + ingest pipeline + retrieval.

    Default path: SKILLCORPUS_HOME (~/.skillcorpus, env-overridable).
    Passing lib_root explicitly switches to another instance.
    """

    def __init__(
        self, lib_root: str | Path | None = None,
        config_path: str | Path | None = None,
    ):
        self.lib_root = Path(lib_root or SKILLCORPUS_HOME).resolve()
        self.lib_root.mkdir(parents=True, exist_ok=True)
        self.config_path = Path(config_path) if config_path else self._default_config_path()
        self.config: dict[str, Any] = {}
        self.store: SkillStore | None = None
        self.classifier: Classifier | None = None
        self.embedder: EmbeddingClient | None = None
        self.ingester: Ingester | None = None

    def _default_config_path(self) -> Path:
        local = self.lib_root / "config.yaml"
        if local.exists():
            return local
        pkg_default = Path(__file__).parent / "config.yaml"
        return pkg_default

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> "SkillLibrary":
        """Initialize the DB + load config + components."""
        self.config = load_config(self.config_path)

        # --- Embedding ---
        embed_cfg = self.config.get("embedding", {})
        dim = int(embed_cfg.get("dim", 1536))
        self.embedder = EmbeddingClient(
            dim=dim,
            base_url=embed_cfg.get("base_url"),
            api_key=embed_cfg.get("api_key"),
            batch_size=int(embed_cfg.get("batch_size", 32)),
            timeout=int(embed_cfg.get("timeout", 60)),
        )

        # --- Storage (bound to the embedding dim) ---
        self.store = SkillStore(self.lib_root / "index.db", embedding_dim=dim)
        self.store.init_schema()

        # --- LLM client + LLM classifier ---
        llm_cfg_dict = self.config.get("llm", {}) or {}
        self.llm: LLMClient | None = None

        if llm_cfg_dict:
            # single endpoint: take endpoints[0], fall back to the llm top-level base_url/model/api_key
            eps = llm_cfg_dict.get("endpoints") or []
            ep0 = eps[0] if eps else {}
            self.llm = LLMClient(
                base_url=ep0.get("base_url") or llm_cfg_dict.get("base_url", "http://localhost:8211/v1"),
                model=ep0.get("model") or llm_cfg_dict.get("model", "qwen3"),
                api_key=ep0.get("api_key") or llm_cfg_dict.get("api_key", "dummy"),
                temperature=float(llm_cfg_dict.get("temperature", 0.1)),
                max_tokens=int(llm_cfg_dict.get("max_tokens", 512)),
                timeout=int(llm_cfg_dict.get("timeout", 60)),
                enable_thinking=bool(llm_cfg_dict.get("enable_thinking", False)),
            )
            if self.llm.is_available():
                self.classifier = Classifier(self.llm)
                logger.info("LLM classifier enabled (model=%s)", llm_cfg_dict.get("model"))
            else:
                logger.warning("LLM unavailable; ingest will set category=OTHER")

        # --- LLM dup judge (Round A — LLM arbitration for cross-source near-dup) ---
        self.dup_judge: LLMDupJudge | None = None
        # --- LLM quality judge (Round B — quality 0-10 scoring) ---
        self.quality_judge: LLMQualityJudge | None = None
        if self.llm is not None and self.llm.is_available():
            try:
                self.dup_judge = LLMDupJudge(self.llm, self.store._connect())
                logger.info("LLM dup judge enabled")
            except Exception as e:
                logger.warning(f"LLM dup judge init failed: {e}")
            try:
                self.quality_judge = LLMQualityJudge(self.llm, self.store._connect())
                logger.info("LLM quality judge enabled")
            except Exception as e:
                logger.warning(f"LLM quality judge init failed: {e}")

        # --- Ingester ---
        concurrency = int(llm_cfg_dict.get("concurrency", 8)) if llm_cfg_dict else 8
        self.ingester = Ingester(
            store=self.store,
            lib_root=self.lib_root,
            source_weights=self.config.get("source_weights", {}),
            thresholds=self.config.get("thresholds", {}),
            embedding_client=self.embedder,
            classifier=self.classifier,
            concurrency=concurrency,
            dup_judge=self.dup_judge,
            dedup_cfg=self.config.get("dedup", {}),
            quality_judge=self.quality_judge,
            quality_cfg=self.config.get("quality", {}),
        )
        return self

    def close(self) -> None:
        if self.store is not None:
            self.store.close()

    def __enter__(self) -> "SkillLibrary":
        return self.open()

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def add(
        self, skill_dir: str | Path, source: str,
        source_url: str | None = None, force: bool = False,
    ) -> IngestResult:
        assert self.ingester is not None, "call open() first"
        return self.ingester.ingest(
            Path(skill_dir), source=source, source_url=source_url, force=force,
        )

    def add_batch(
        self, root: str | Path, source: str,
        pattern: str = "**/SKILL.md",
        limit: int | None = None,
        concurrent: bool = True,
        source_url: str | None = None,
    ) -> dict[str, Any]:
        assert self.ingester is not None, "call open() first"
        if concurrent and self.ingester.concurrency > 1:
            return self.ingester.ingest_batch_concurrent(
                Path(root), source=source, pattern=pattern, limit=limit,
                source_url=source_url,
            )
        return self.ingester.ingest_batch(
            Path(root), source=source, pattern=pattern, limit=limit,
            source_url=source_url,
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, skill_id: str) -> SkillRecord | None:
        assert self.store is not None
        return self.store.get(skill_id)

    def list(
        self,
        category: str | None = None,
        source: str | None = None,
        tag: str | None = None,
        min_quality: float = 0.0,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SkillRecord]:
        assert self.store is not None
        return self.store.list(
            category=category, source=source, tag=tag,
            min_quality=min_quality, limit=limit, offset=offset,
        )

    def stats(self) -> dict[str, Any]:
        assert self.store is not None
        s = self.store.stats()
        s["lib_root"] = str(self.lib_root)
        s["has_embedding"] = self.embedder.is_available() if self.embedder else False
        s["has_llm_classify"] = self.classifier is not None
        s["has_dup_judge"] = self.dup_judge is not None
        s["has_quality_judge"] = self.quality_judge is not None
        if self.dup_judge is not None:
            try:
                s["dedup_judgments"] = self.dup_judge.stats()
            except Exception as e:
                logger.debug("dup_judge.stats() unavailable: %s", e)
        if self.quality_judge is not None:
            try:
                s["quality_judgments"] = self.quality_judge.stats()
                s["quality_histogram"] = self.quality_judge.histogram()
            except Exception as e:
                logger.debug("quality_judge.stats() unavailable: %s", e)
        # superseded count — the payoff of Round A near-dup detection
        conn = self.store._connect()
        row = conn.execute(
            "SELECT COUNT(*) FROM skills WHERE superseded_by IS NOT NULL"
        ).fetchone()
        s["superseded_count"] = int(row[0]) if row else 0

        # number of skills with description > 1024 (Round C alerting metric)
        desc_max = int((self.config.get("thresholds") or {}).get("description_max_chars", 1024))
        row = conn.execute(
            "SELECT COUNT(*) FROM skills WHERE deleted = 0 AND LENGTH(description) > ?",
            (desc_max,),
        ).fetchone()
        s["overlong_description_count"] = int(row[0]) if row else 0
        return s


# ---------------------------------------------------------------------------
# Build pipeline (relocated from scripts/refresh_loop.py; cadence dropped)
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_YAML = Path(__file__).resolve().parent / "sources.yaml"


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8"))


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
        [sys.executable, "-m", "skillcorpus.aggregate.converters.lobehub",
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
    _run_module("skillcorpus.curate.quality_pass", ["--lib", lib_root, "--workers", "16"])

    print("\n→ dedup_pass (cross-source near-dup merge)...", flush=True)
    _run_module("skillcorpus.curate.dedup_pass", ["--lib", lib_root])

    print("\n→ license_audit activate (GREEN whitelist → active)...", flush=True)
    _run_module("skillcorpus.curate.license_audit", ["activate", "--db", db_path])

    print("\n→ export.corpus (parquet + attachments + card)...", flush=True)
    from skillcorpus.export.corpus import write_corpus
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
    sources = load_registry(config)
    defaults = _load_yaml(Path(config)).get("defaults", {}) or {}
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
            src_dir, git_status = clone_or_pull(owner, repo)
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
    python -m skillcorpus.cli build                           # demo build from scratch
    python -m skillcorpus.cli build --full                    # full registry (production)
    python -m skillcorpus.cli build --sources-config X.yaml    # bring your own registry
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
