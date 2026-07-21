"""Skill Library CLI — click 命令行接口.

用法示例 (``--lib`` 是 group 选项, 须放在子命令之前):
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

from .pipeline import SkillLibrary


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
    """初始化一个 skill 库目录 (路径由 --lib 指定)."""
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
    """添加单个 skill 目录到库."""
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
    """批量扫描目录, 对每个 SKILL.md 所在 dir 入库."""
    with SkillLibrary(ctx.obj["lib_root"], ctx.obj["config_path"]) as lib:
        out = lib.add_batch(root, source=source, pattern=pattern, limit=limit)
        samples = out.pop("rejected_samples", [])
        added_ids = out.pop("added_ids", [])
        click.echo(json.dumps(out, ensure_ascii=False, indent=2))
        if samples:
            click.echo("\nRejected samples (first 10):")
            for path, reason in samples[:10]:
                click.echo(f"  - {path}: {reason}")


# NOTE: 本 CLI 只覆盖入库/构建/运维; runtime 检索 (BM25+embedding 搜库)
# 由 consumer 端负责, 不在这里提供 `search` 子命令。


@cli.command()
@click.argument("skill_id")
@click.pass_context
def get(ctx, skill_id):
    """显示单个 skill 详情."""
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
    """列出 skill (可按 category/source/tag/quality 过滤)."""
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
    """删除 skill (默认 soft)."""
    with SkillLibrary(ctx.obj["lib_root"], ctx.obj["config_path"]) as lib:
        ok = lib.delete(skill_id, soft=not hard)
        click.echo("deleted" if ok else "not found")
        sys.exit(0 if ok else 1)


@cli.command()
@click.pass_context
def stats(ctx):
    """库统计信息."""
    with SkillLibrary(ctx.obj["lib_root"], ctx.obj["config_path"]) as lib:
        click.echo(json.dumps(lib.stats(), ensure_ascii=False, indent=2))


@cli.command("build")
@click.option("--update", is_flag=True,
              help="增量模式: 按 cadence 只跑到期的源 (默认从零全跑)")
@click.option("--full", is_flag=True,
              help="用全量注册表 sources.full.yaml (= --sources-config 的快捷方式)")
@click.option("--sources-config", "sources_config", type=click.Path(), default=None,
              help="源注册表 yaml 路径 (默认公开 demo sources.yaml)")
@click.option("--source", default=None, help="只跑指定源名")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def build(ctx, update, full, sources_config, source, dry_run):
    """一键全流程 — discover→clone→ingest→quality→export, 从零产出 / 增量更新.

    空库自动建 (SkillLibrary.open 内置 init_schema)。是 refresh_loop 的门面。
    \b
    python -m skill_library.cli build                       # demo 从零产出
    python -m skill_library.cli build --update              # 增量 (按 cadence)
    python -m skill_library.cli build --full                # 全量注册表 (生产)
    python -m skill_library.cli build --sources-config X.yaml  # 自备注册表
    """
    from pathlib import Path
    from .scripts.refresh_loop import run_refresh, DEFAULT_YAML
    # 注册表选择: --sources-config 显式 > --full 快捷 > 默认 demo
    if sources_config:
        config = Path(sources_config)
    elif full:
        config = Path(__file__).resolve().parent / "sources.full.yaml"
    else:
        config = DEFAULT_YAML
    if not config.exists():
        if sources_config:
            hint = f"注册表文件不存在: {config}"
        else:  # 来自 --full
            hint = ("全量注册表 sources.full.yaml 不随公开发布提供; "
                    "公开版用默认 demo (去掉 --full), 或 --sources-config 指向你自己的 yaml")
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
    """更新 tag 列表 (逗号分隔)."""
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
    """修改主分类."""
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
    """导出 skill 子集为 zip (含 manifest.json + skill 目录)."""
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
