"""The plugin's own model client, over real HTTP.

A local server speaking `/chat/completions` stands in for the provider, so
everything between the engine's `ChatModel` port and the socket is the
shipping code. No credential, no network.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location("skillsearch_hermes_model", PLUGIN_ROOT / "model.py")
model_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model_module)
OpenAICompatibleModel = model_module.OpenAICompatibleModel


class Handler(BaseHTTPRequestHandler):
    replies: list[str] = []
    seen: list[dict[str, Any]] = []

    def do_POST(self) -> None:  # noqa: N802 — the stdlib spells it this way
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        Handler.seen.append({"auth": self.headers.get("Authorization"), "body": body})
        if body.get("model") == "boom":
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b'{"error": "upstream unavailable"}')
            return
        content = Handler.replies.pop(0) if Handler.replies else "{}"
        payload = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def provider():
    Handler.replies, Handler.seen = [], []
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1", Handler
    server.shutdown()


def test_the_client_sends_the_credential_and_the_model(provider) -> None:
    base_url, handler = provider
    handler.replies.append("hello")
    client = OpenAICompatibleModel(base_url=base_url, api_key="sk-test", model="m1")

    out = asyncio.run(client.complete([{"role": "user", "content": "ping"}]))

    assert out == "hello"
    assert handler.seen[0]["auth"] == "Bearer sk-test"
    assert handler.seen[0]["body"]["model"] == "m1"


def test_an_http_error_raises_for_the_caller_to_fall_back_on(provider) -> None:
    base_url, _ = provider
    client = OpenAICompatibleModel(base_url=base_url, api_key="", model="boom")
    with pytest.raises(RuntimeError, match="HTTP 503"):
        asyncio.run(client.complete([{"role": "user", "content": "ping"}]))


def test_an_unreachable_endpoint_raises_rather_than_hanging() -> None:
    client = OpenAICompatibleModel(base_url="http://127.0.0.1:1/v1", model="m1", timeout_s=2.0)
    with pytest.raises(RuntimeError, match="unreachable"):
        asyncio.run(client.complete([{"role": "user", "content": "ping"}]))


def test_the_gate_runs_over_real_http_end_to_end(tmp_path: Path, provider) -> None:
    """The whole pipeline with a configured endpoint: rewriter, then gate."""
    base_url, handler = provider
    handler.replies.extend(
        [
            '{"need_retrieval": true, "rewritten_query": "fill a pdf acroform"}',
            '{"plan": "fill the form", "skills": ["local/pdf-forms"]}',
        ]
    )
    sys.path.insert(0, str(PLUGIN_ROOT.parent / "python"))
    spec = importlib.util.spec_from_file_location(
        "skillsearch_hermes_plugin",
        PLUGIN_ROOT / "__init__.py",
        submodule_search_locations=[str(PLUGIN_ROOT)],
    )
    plugin = importlib.util.module_from_spec(spec)
    sys.modules["skillsearch_hermes_plugin"] = plugin
    spec.loader.exec_module(plugin)

    skills = tmp_path / "skills"
    for name, description, body in [
        ("pdf-forms", "Fill PDF acroforms", "Run pdftk with an FDF."),
        ("git-bisect", "Find the commit that broke a test", "Run git bisect start."),
    ]:
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n", encoding="utf-8"
        )
    (tmp_path / "skillsearch.json").write_text(
        json.dumps(
            {
                "skills_dir": str(skills),
                "model": "gate-model",
                "model_base_url": base_url,
                "model_api_key": "sk-test",
            }
        ),
        encoding="utf-8",
    )

    provider_obj = plugin.SkillSearchProvider()
    provider_obj.initialize("session-1", hermes_home=str(tmp_path))
    try:
        block = provider_obj.prefetch("can you fill in /tmp/a7f2.pdf for me")
    finally:
        provider_obj.shutdown()

    assert "### Skill: pdf-forms" in block
    assert "git-bisect" not in block
    assert len(handler.seen) == 2, "the rewriter and the gate are each called once"
    assert "You are a skill selector" in handler.seen[1]["body"]["messages"][-1]["content"]
