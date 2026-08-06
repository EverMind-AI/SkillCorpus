"""core.paths — the single place that resolves ``SKILLCORPUS_HOME``.

Everything the pipeline writes (clone cache, index.db, the attachment tree,
run state, export staging) lives under ``SKILLCORPUS_HOME``, which is outside
the repository and overridable via the environment variable. Config / registry
files are repo inputs and live in ``configs/`` instead — they are not here.

Default: ``~/.skillcorpus``. Set ``SKILLCORPUS_HOME`` to relocate (e.g. a large
data disk in production).
"""
from __future__ import annotations

import os
from pathlib import Path

SKILLCORPUS_HOME = Path(
    os.environ.get("SKILLCORPUS_HOME") or (Path.home() / ".skillcorpus")
).expanduser().resolve()

# The library root == SKILLCORPUS_HOME; the store/fsstore hang their files off it.
CACHE_DIR = SKILLCORPUS_HOME / "cache"      # <owner>/<repo> git clone cache
INDEX_DB = SKILLCORPUS_HOME / "index.db"    # SQLite + vec_skills + faiss sidecar


# --- Repo-relative inputs (hand-written configs + license audit artifacts) ---
# Resolvable only in a cloned / editable checkout; NOT shipped as package data.
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = REPO_ROOT / "configs"
AUDIT_DIR = REPO_ROOT / "audit"
DEFAULT_CONFIG = CONFIGS_DIR / "default.yaml"
DEFAULT_SOURCES = CONFIGS_DIR / "sources.demo.yaml"
SOURCES_FULL = CONFIGS_DIR / "sources.full.yaml"
LICENSE_WHITELIST = AUDIT_DIR / "license_safe_sources.json"
