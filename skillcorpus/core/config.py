"""core.config — load and lightly validate the pipeline config (config.yaml).

The config carries endpoint / concurrency / dim settings for the LLM and
embedding clients. This is the one place that reads it so the schema checks
live next to the load.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_config(path: str | Path) -> dict[str, Any]:
    """Parse config.yaml into a dict, validating the shapes the pipeline relies on."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"config not found: {p}. Configs live in configs/ at the repo root; run from a "
            "clone / editable install (pip install -e .), or pass --config explicitly."
        )
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"config must be a mapping, got {type(data).__name__}: {p}")
    for section in ("embedding", "llm"):
        val = data.get(section)
        if val is not None and not isinstance(val, dict):
            raise ValueError(f"config.{section} must be a mapping (in {p})")
    return data
