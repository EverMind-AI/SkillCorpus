"""The provider against a fake host driving the real Hermes contract.

The host is not importable here, so the contract is pinned instead: the
method names, signatures and return types Hermes calls, read off the
providers it already ships. A drift in either direction fails here rather
than at a user's first turn.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT.parent / "engine-python"))


def load_plugin() -> Any:
    """Import the plugin package by path, as the host loads it from disk."""
    spec = importlib.util.spec_from_file_location(
        "skillsearch_hermes", PLUGIN_ROOT / "__init__.py", submodule_search_locations=[str(PLUGIN_ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["skillsearch_hermes"] = module
    spec.loader.exec_module(module)
    return module


plugin = load_plugin()


class FakeContext:
    """What Hermes hands `register`, reduced to what this plugin reads."""

    def __init__(self) -> None:
        self.provider: Any = None

    def register_memory_provider(self, provider: Any) -> None:
        self.provider = provider


def write_skill(root: Path, name: str, description: str, body: str) -> None:
    (root / name).mkdir(parents=True)
    (root / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n", encoding="utf-8"
    )


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A `$HERMES_HOME` with a skills directory and a config pointing at it."""
    skills = tmp_path / "skills"
    write_skill(skills, "pdf-forms", "Fill PDF acroforms", "Run pdftk with an FDF.")
    write_skill(skills, "git-bisect", "Find the commit that broke a test", "Run git bisect start.")
    (tmp_path / "skillsearch.json").write_text(
        json.dumps({"skills_dir": str(skills), "top_k": 2}), encoding="utf-8"
    )
    return tmp_path


def test_register_claims_the_memory_slot() -> None:
    ctx = FakeContext()
    plugin.register(ctx)
    assert isinstance(ctx.provider, plugin.SkillSearchProvider)
    assert ctx.provider.name == "skillsearch"


def test_the_provider_implements_every_method_the_host_calls() -> None:
    """The contract, read off the memory providers Hermes already ships."""
    provider = plugin.SkillSearchProvider()
    for method in (
        "is_available",
        "initialize",
        "shutdown",
        "prefetch",
        "get_tool_schemas",
        "get_config_schema",
        "save_config",
    ):
        assert callable(getattr(provider, method)), method
    assert isinstance(type(provider).name, property), "name is a property on the host contract"


def test_is_available_makes_no_network_call() -> None:
    """The host contract forbids it, and a provider that dials out on selection stalls startup."""
    provider = plugin.SkillSearchProvider()
    import socket

    original = socket.socket

    def forbidden(*args: Any, **kwargs: Any):
        raise AssertionError("is_available opened a socket")

    socket.socket = forbidden  # type: ignore[assignment]
    try:
        assert provider.is_available() is True
    finally:
        socket.socket = original


def test_prefetch_ranks_the_matching_skill_first(home: Path) -> None:
    """No model configured, so no gate: the block is the top of the ranking.

    Both skills appear because `top_k` is 2 and nothing narrows — which is
    the documented unfiltered mode, not an accident. What retrieval owes
    here is the order.
    """
    provider = plugin.SkillSearchProvider()
    provider.initialize("session-1", hermes_home=str(home))
    try:
        block = provider.prefetch("fill in the acroform with pdftk")
    finally:
        provider.shutdown()

    assert "pdftk" in block
    assert block.index("pdf-forms") < block.index("git-bisect")


def test_a_host_model_client_narrows_the_block_to_the_gate_s_pick(home: Path) -> None:
    """With a model, the gate runs and an unrelated skill is dropped.

    The fake answers the rewriter then the gate, in the order the pipeline
    calls them — which is also what pins that order.
    """
    replies = [
        '{"need_retrieval": true, "rewritten_query": "fill a pdf acroform"}',
        '{"plan": "fill the form", "skills": ["local/pdf-forms"]}',
    ]
    seen: list[str] = []

    class FakeModel:
        async def complete(self, messages, *, model=None, temperature=0.0, max_tokens=8192):
            seen.append(messages[-1]["content"])
            return replies.pop(0) if replies else "{}"

    class HostWithModel:
        model_client = FakeModel()

    # Both are required: a client with no `model` configured leaves the
    # engine unfiltered, because the model name is what selects the route.
    config = json.loads((home / "skillsearch.json").read_text(encoding="utf-8"))
    config["model"] = "fake-model"
    # Explicit: with only a local directory the gate is off by default, and
    # this test is about the gate.
    config["gate"] = True
    (home / "skillsearch.json").write_text(json.dumps(config), encoding="utf-8")

    provider = plugin.SkillSearchProvider(HostWithModel())
    provider.initialize("session-1", hermes_home=str(home))
    try:
        block = provider.prefetch("can you fill in /tmp/a7f2.pdf for me")
    finally:
        provider.shutdown()

    assert "### Skill: pdf-forms" in block
    assert "git-bisect" not in block
    assert len(seen) == 2, "the rewriter and the gate are each called once"
    assert "You are a skill selector" in seen[1]


def test_a_model_client_without_a_configured_model_stays_unfiltered(home: Path) -> None:
    """The other half of the pairing, so neither half can drift alone."""

    class FakeModel:
        async def complete(self, messages, *, model=None, temperature=0.0, max_tokens=8192):
            raise AssertionError("no model is configured; nothing should call one")

    class HostWithModel:
        model_client = FakeModel()

    provider = plugin.SkillSearchProvider(HostWithModel())
    provider.initialize("session-1", hermes_home=str(home))
    try:
        assert "pdf-forms" in provider.prefetch("fill in the acroform with pdftk")
    finally:
        provider.shutdown()


def test_prefetch_returns_empty_rather_than_raising_before_initialize() -> None:
    assert plugin.SkillSearchProvider().prefetch("anything") == ""


def test_prefetch_survives_an_engine_that_raises(home: Path) -> None:
    """The hot-path contract: a broken engine costs the turn its skills, not the turn."""
    provider = plugin.SkillSearchProvider()
    provider.initialize("session-1", hermes_home=str(home))

    class Exploding:
        def prefetch(self, query: str) -> str:
            raise RuntimeError("engine down")

    provider._adapter = Exploding()
    assert provider.prefetch("fill in the acroform") == ""


def test_a_blank_query_searches_nothing(home: Path) -> None:
    provider = plugin.SkillSearchProvider()
    provider.initialize("session-1", hermes_home=str(home))
    try:
        assert provider.prefetch("   ") == ""
    finally:
        provider.shutdown()


def test_a_missing_config_enables_public_marketplaces_by_default(tmp_path: Path) -> None:
    from engine_adapter import load_config

    config = load_config(str(tmp_path))
    assert config.clawhub_endpoint == "https://clawhub.ai"
    assert config.skillhub_cn_endpoint == "https://api.skillhub.cn"


def test_a_malformed_config_disables_retrieval_without_raising(tmp_path: Path) -> None:
    (tmp_path / "skillsearch.json").write_text("{ not json", encoding="utf-8")
    provider = plugin.SkillSearchProvider()
    provider.initialize("session-1", hermes_home=str(tmp_path))
    try:
        assert provider.prefetch("anything") == ""
    finally:
        provider.shutdown()


def test_save_config_merges_rather_than_replaces(tmp_path: Path) -> None:
    provider = plugin.SkillSearchProvider()
    provider.save_config({"skills_dir": "/a", "model": "m"}, str(tmp_path))
    # A skipped prompt arrives as None and must leave the stored value alone.
    provider.save_config({"skills_dir": None, "top_k": 3}, str(tmp_path))

    stored = json.loads((tmp_path / "skillsearch.json").read_text(encoding="utf-8"))
    assert stored == {"skills_dir": "/a", "model": "m", "top_k": 3}


def test_the_config_schema_covers_every_documented_key() -> None:
    keys = {field["key"] for field in plugin.SkillSearchProvider().get_config_schema()}
    assert {"skills_dir", "hub_endpoint", "model", "top_k", "max_select"} <= keys


def test_no_model_callable_tools_are_published() -> None:
    """Retrieval is automatic; a tool would be one more thing to remember to press."""
    assert plugin.SkillSearchProvider().get_tool_schemas() == []
