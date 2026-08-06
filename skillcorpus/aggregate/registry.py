"""aggregate.registry — parse the source registry (sources.yaml) and validate types."""
from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_YAML = Path(__file__).resolve().parents[1] / "sources.yaml"

VALID_TYPES = {
    "git_clone", "readme_scrape", "index_api",
    "json_catalog", "sitemap_scrape", "lobehub_json",
}


def load_registry(yaml_path: Path | str = DEFAULT_YAML) -> list[dict]:
    """Read sources.yaml and return the entry list (each with name/repo/type/...)."""
    cfg = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8"))
    sources = cfg.get("sources", []) or []
    for s in sources:
        t = s.get("type")
        if t not in VALID_TYPES:
            raise ValueError(f"unknown source type {t!r} in entry {s.get('name')}")
    return sources
