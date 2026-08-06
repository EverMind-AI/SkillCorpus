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
SKILLS_DIR = SKILLCORPUS_HOME / "skills"    # <source>/<name>/ attachment tree
STATE_DIR = SKILLCORPUS_HOME / "state"      # run state
EXPORT_DIR = SKILLCORPUS_HOME / "export"    # export staging
