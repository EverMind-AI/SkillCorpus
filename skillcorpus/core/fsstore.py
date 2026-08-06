"""core.fsstore — filesystem helpers: copy/remove a skill dir under the library root."""
from __future__ import annotations

import json
import shutil
from pathlib import Path


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


def copy_skill_to_library(
    src_dir: Path, lib_root: Path, source: str, name_slug: str,
    meta: dict | None = None,
) -> Path:
    """Copy the skill directory from src_dir to lib_root/skills/<source>/<name_slug>/.

    Preserves SKILL.md + scripts/ + references/ + all other files.
    Appends a .meta.json recording the ingest metadata.
    """
    dst_dir = lib_root / "skills" / source / name_slug
    if dst_dir.exists():
        shutil.rmtree(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)

    for item in src_dir.iterdir():
        # skip dotfiles and symlinks: a scraped repo must not pull
        # build-machine files into the published attachments
        if item.name.startswith(".") or item.is_symlink():
            continue
        if item.is_file():
            shutil.copy2(item, dst_dir / item.name)
        elif item.is_dir():
            shutil.copytree(item, dst_dir / item.name, symlinks=True)

    if meta:
        (dst_dir / ".meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return dst_dir


def remove_skill_from_library(lib_root: Path, stored_path: str) -> bool:
    full = lib_root / stored_path
    if full.exists() and full.is_dir():
        shutil.rmtree(full)
        return True
    return False
