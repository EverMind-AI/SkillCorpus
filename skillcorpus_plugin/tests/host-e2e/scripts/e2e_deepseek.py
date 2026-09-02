"""Case P1 in both modes, through the real DeepSeek Harness.

The harness loads the plugin as an ordinary `insert:` row in a
`dsh --profile headless --patch` overlay, so `agent/pre-step` and
`ctx.tools.register` both come from the real runtime rather than a hand-built
context.

    auto       pre-step injects, `skill_search` is absent from the tool surface
    on_demand  the tool is offered, nothing is injected, and the model has to
               decide to call it

A recording proxy sits between the harness and the model deployment. It is the
only place that sees what the harness actually sent, so it — not the plugin's
own log line — is what answers "was the tool registered" and "did the skill
body reach the model". It also turns thinking off upstream, for the reason in
`_e2e.thinking_off`.

Usage:

    export SKILLSEARCH_E2E_BASE_URL=... SKILLSEARCH_E2E_MODEL=...
    python e2e_deepseek.py --host /path/to/deepseek-harness [auto|on_demand|default ...]

`default` writes no `mode` key at all, which exercises the Config schema's own
default rather than an explicitly written mode — that is how 0.3.0's change of
default is checked rather than asserted.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _e2e

RUN_TIMEOUT_S = 600.0


# --------------------------------------------------------------------------
# recording proxy


class Recorder:
    """Every `/chat/completions` the harness sent, in order."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.lock = threading.Lock()

    def note(self, payload: dict) -> None:
        tools = [
            (t.get("function") or {}).get("name")
            for t in (payload.get("tools") or [])
            if isinstance(t, dict)
        ]
        with self.lock:
            self.requests.append({
                "tools": tools,
                "n_messages": len(payload.get("messages") or []),
                "messages": payload.get("messages"),
            })


def make_handler(recorder: Recorder, upstream_url: str, api_key: str):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _forward(self, body: bytes | None) -> None:
            path = self.path
            if body is not None:
                try:
                    payload = json.loads(body, strict=False)
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, dict):
                    if path.endswith("/chat/completions"):
                        recorder.note(payload)
                    payload.update(_e2e.thinking_off())
                    body = json.dumps(payload).encode()
            req = urllib.request.Request(
                f"{upstream_url}{path}", data=body,
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                method="POST" if body is not None else "GET",
            )
            try:
                upstream = urllib.request.urlopen(req, timeout=900)
            except Exception as exc:
                detail = ""
                if hasattr(exc, "read"):
                    try:
                        detail = exc.read().decode("utf-8", "replace")[:1000]
                    except Exception:
                        detail = ""
                sys.stderr.write(f"[proxy] {path} upstream failed {exc}\n{detail}\n")
                out = json.dumps({"error": str(exc), "upstream": detail}).encode()
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
                return

            content_type = upstream.headers.get("Content-Type", "application/json")
            with upstream:
                if "event-stream" in content_type:
                    self.send_response(upstream.status)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.close_connection = True
                    while chunk := upstream.read(8192):
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    return
                out = upstream.read()
                self.send_response(upstream.status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        def do_POST(self) -> None:
            self._forward(self.rfile.read(int(self.headers.get("Content-Length", 0))))

        def do_GET(self) -> None:
            self._forward(None)

        def log_message(self, *args: object) -> None:
            pass

    return Handler


def start_proxy(upstream_url: str, api_key: str) -> tuple[ThreadingHTTPServer, Recorder, str]:
    recorder = Recorder()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = ThreadingHTTPServer(
        ("127.0.0.1", port), make_handler(recorder, upstream_url, api_key)
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, recorder, f"http://127.0.0.1:{port}"


# --------------------------------------------------------------------------
# overlay


def patch_file(mode: str, base_url: str, skills_dir: Path, model: str,
               broken_source: bool = False) -> Path:
    entries: list[dict] = [
        # Disabled because this install ships their types but no JS.
        {"id": "typert", "disabled": True},
        {"id": "typert-loader", "disabled": True},
        {"id": "typert-gateway", "disabled": True},
        # The catalogue tool publishes the same skills from the other
        # direction; leaving it on would let a fixture hit come from `skill`.
        {"id": "tool-skill", "disabled": True},
        {"id": "llm-deepseek",
         "config": {"apiKeyEnv": "DSH_E2E_KEY", "baseURL": base_url,
                    "thinking": "disabled", "maxTokens": 8192,
                    "defaultContextWindow": 262144,
                    "models": [{"id": model, "contextWindow": 262144,
                                "maxTokens": 8192}]}},
        {"id": "agent-default-model",
         "config": {"provider": "deepseek-official", "model": model}},
        {"insert": [
            {"id": "skill-search", "name": "@deepseek-ai/dsh-skill-search",
             "config": {
                 **({} if mode == "default" else {"mode": mode}),
                 "skillsDirs": [str(skills_dir)],
                 # Every remote catalogue off — see the note in
                 # `e2e_hermes.py`. Under `--broken-source` the first one
                 # points at a closed port instead, which is case P5.
                 **_e2e.camel(_e2e.source_endpoints(broken=broken_source)),
                 "topK": 1,
                 "maxSelect": 1,
                 "gate": False,
                 "provider": "deepseek-official",
                 "model": model,
                 "rewriteTimeoutMs": 60000,
                 "gateTimeoutMs": 120000,
             }},
        ]},
    ]
    path = Path(tempfile.mkdtemp(prefix=f"dsh-{mode}-")) / "patch.yml"
    path.write_text(json.dumps(entries, indent=1), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# one run


def _decode(stream: bytes | str | None) -> str:
    """`TimeoutExpired` carries bytes or str depending on how it was raised."""
    if isinstance(stream, bytes):
        return stream.decode("utf-8", "replace")
    return stream or ""


def _role_text(requests: list[dict], role: str) -> str:
    parts: list[str] = []
    for request in requests:
        for message in request.get("messages") or []:
            if not isinstance(message, dict) or message.get("role") != role:
                continue
            content = message.get("content")
            parts.append(content if isinstance(content, str)
                         else json.dumps(content, ensure_ascii=False))
    return "\n".join(parts)


def run(mode: str, skills: Path, base_url: str, recorder: Recorder, dsh_bin: Path,
        model: dict, prompt: str, broken_source: bool = False) -> dict:
    before = len(recorder.requests)
    patch = patch_file(mode, base_url, skills, model["model"], broken_source)
    workspace = Path(tempfile.mkdtemp(prefix=f"dsh-ws-{mode}-"))
    env = {**os.environ, "DSH_E2E_KEY": model["api_key"], "DSH_SNAPSHOT": "1"}
    out: dict = {"mode": mode}
    started = time.time()
    try:
        p = subprocess.run(
            ["node", str(dsh_bin), "--profile", "headless", "--patch", str(patch),
             prompt],
            capture_output=True, text=True, cwd=str(workspace),
            timeout=RUN_TIMEOUT_S, check=False, env=env,
        )
        out["status"] = "completed" if p.returncode == 0 else "error"
        out["returncode"] = p.returncode
        out["reply"] = p.stdout
        out["stderr"] = p.stderr[-4000:]
    except subprocess.TimeoutExpired as exc:
        out["status"] = "timeout"
        out["reply"] = _decode(exc.stdout)
        out["stderr"] = _decode(exc.stderr)[-4000:]
    out["elapsed_s"] = round(time.time() - started, 1)

    requests = recorder.requests[before:]
    # The agent's own tool surface is the request carrying the user's task; the
    # rewriter's internal call sends no tools at all.
    agent_requests = [r for r in requests if r["tools"]]
    out["n_requests"] = len(requests)
    out["tool_offered"] = any("skill_search" in r["tools"] for r in agent_requests)
    # The harness replays the whole conversation each step, so the last request
    # carries every tool call the model made. Reading them off the wire needs
    # no session log, which headless does not persist.
    last = agent_requests[-1] if agent_requests else None
    out["skill_search_calls"] = [
        (call.get("function") or {}).get("arguments")
        for message in ((last or {}).get("messages") or [])
        if isinstance(message, dict)
        for call in (message.get("tool_calls") or [])
        if isinstance(call, dict) and (call.get("function") or {}).get("name") == "skill_search"
    ]
    # Which channel carried the body. A `user` message is the pre-step
    # injection, a `tool` message is the tool result — and these are what tell
    # the two modes apart, since the reply looks the same either way.
    out["injected_text"] = _role_text(requests, "user")
    out["tool_result_text"] = _role_text(requests, "tool")
    shutil.rmtree(Path(patch).parent, ignore_errors=True)
    shutil.rmtree(workspace, ignore_errors=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("modes", nargs="*", default=["auto", "on_demand"])
    ap.add_argument("--host", type=Path,
                    default=os.environ.get("SKILLSEARCH_E2E_DSH_CHECKOUT"),
                    help="a DeepSeek Harness checkout with the plugin installed")
    ap.add_argument("--case", default="p1", choices=sorted(_e2e.CASES),
                    help="which case in cases.md to run; each swaps the prompt "
                         "and what the verdict requires")
    ap.add_argument("--broken-source", action="store_true",
                    help="case P5: point one remote catalogue at a closed port "
                         "and check the local corpus, the turn and the log all "
                         "survive it")
    ap.add_argument("--dump", type=Path, default=None)
    args = ap.parse_args()
    if not args.host:
        ap.error("--host or SKILLSEARCH_E2E_DSH_CHECKOUT is required")

    dsh_bin = Path(args.host) / "apps" / "cli" / "lib" / "bin.js"
    if not dsh_bin.exists():
        print(f"missing {dsh_bin} — build the harness first")
        return 1

    model = _e2e.model_config()
    server, recorder, base_url = start_proxy(model["base_url"], model["api_key"])
    skills = _e2e.corpus()
    print(f"host={args.host} model={model['model']} corpus={skills} proxy={base_url}")

    results: dict[str, dict] = {}
    failures: list[str] = []
    try:
        for mode in args.modes:
            out = run(mode, skills, base_url, recorder, dsh_bin, model,
                      _e2e.CASES[args.case]["prompt"], args.broken_source)
            # `default` has to behave as on-demand; that is the assertion.
            effective = "on_demand" if mode == "default" else mode
            ok, facts = _e2e.verdict(
                case=args.case,
                mode=effective,
                tool_offered=out["tool_offered"],
                auto_channel_filled=_e2e.sentinel_in(out["injected_text"]),
                tool_called=bool(out["skill_search_calls"]),
                delivered=out["injected_text"] + "\n" + out["tool_result_text"],
                reply=out["reply"],
            )
            out["verdict"] = {"pass": ok, "effective_mode": effective, **facts}
            results[mode] = out
            print(_e2e.line(mode, ok, facts, out["elapsed_s"]))
            if out["status"] != "completed":
                tail = (out.get("stderr") or "")[-600:]
                print(f"             status={out['status']} stderr: {tail}")
            if not ok:
                failures.append(mode)
    finally:
        server.shutdown()

    if args.dump:
        args.dump.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print("all passed" if not failures else f"failed: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
