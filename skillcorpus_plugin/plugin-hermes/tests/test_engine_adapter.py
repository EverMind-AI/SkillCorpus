"""The adapter's prefetch deadline, which comes from its config file.

The plugin offers `timeout_s` in its setup and writes it to
`skillsearch.json`, but the value is not a `SearchConfig` field — it bounds
the adapter's own wait, not the engine's. It used to be routed through
`load_config` and silently dropped, so every deployment ran the 8-second
default however it was configured. These pin the wiring rather than the
number.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT.parent / "engine-python"))


def _load() -> object:
    """Import the adapter the way the plugin does — by path, as a submodule
    of the package the host loads from disk."""
    package = importlib.util.module_from_spec(
        importlib.util.spec_from_file_location(
            "skillsearch_hermes", PLUGIN_ROOT / "__init__.py",
            submodule_search_locations=[str(PLUGIN_ROOT)],
        )
    )
    sys.modules.setdefault("skillsearch_hermes", package)
    spec = importlib.util.spec_from_file_location(
        "skillsearch_hermes.engine_adapter", PLUGIN_ROOT / "engine_adapter.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


adapter = _load()
DEFAULT_PREFETCH_TIMEOUT_S = adapter.DEFAULT_PREFETCH_TIMEOUT_S
SkillSearchEngine = adapter.SkillSearchEngine
load_prefetch_timeout = adapter.load_prefetch_timeout


def write(home: Path, config: dict) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    (home / "skillsearch.json").write_text(json.dumps(config), encoding="utf-8")
    return home


def test_a_configured_deadline_is_the_one_used(tmp_path: Path) -> None:
    assert load_prefetch_timeout(str(write(tmp_path, {"timeout_s": 45}))) == 45.0


def test_the_deadline_reaches_the_engine_the_host_will_call(tmp_path: Path) -> None:
    """The regression: `from_hermes` built the adapter without it."""
    home = write(tmp_path, {"timeout_s": 45, "skills_dir": str(tmp_path / "skills")})
    engine = SkillSearchEngine.from_hermes(object(), hermes_home=str(home))
    assert engine._timeout_s == 45.0


def test_no_config_file_leaves_the_default(tmp_path: Path) -> None:
    assert load_prefetch_timeout(str(tmp_path)) == DEFAULT_PREFETCH_TIMEOUT_S


def test_an_unusable_value_leaves_the_default(tmp_path: Path) -> None:
    """Zero or negative would mean "time out immediately", never "no limit"."""
    for bad in (0, -1, "soon", None):
        assert load_prefetch_timeout(str(write(tmp_path, {"timeout_s": bad}))) == (
            DEFAULT_PREFETCH_TIMEOUT_S
        )
