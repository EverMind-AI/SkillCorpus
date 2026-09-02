"""Case P1 in both modes, through a real Raven `AgentLoop`.

Raven's own `build_plugin_registry` reads the manifest and resolves the
factories; `AgentLoop` runs the turn. What is asserted is what the model said.

    auto       the `skills` context segment fills, the tool is absent
    on_demand  the tool is offered, the segment is not registered, and the
               model has to decide to call it

Usage:

    export SKILLSEARCH_E2E_BASE_URL=... SKILLSEARCH_E2E_MODEL=...
    python e2e_raven.py --host /path/to/raven [auto|on_demand ...]

## Which Raven

`on_demand` runs on stock Raven: `contributes.tools` is an upstream slot.

`auto` does not. It needs `contributes.context_segments` and the
`build_plugin_segments` / `AgentLoop(plugin_segments=...)` wiring behind it,
which is **not** in upstream Raven as of the commit recorded in
`../reports/0.3.0.md`. Against a stock checkout this script reports `auto` as
BLOCKED — a host without the slot, not a plugin that failed. Do not record a
patched-host `auto` PASS as public Raven support.

The script tells the two apart by asking the host for the symbol rather than
by version string, so it stays honest when the slot does land.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import site
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _e2e


def _turn_runner():
    from raven.spine import ChatType, Origin, Source, Text, TurnRequest

    async def _turn(agent, message: str) -> str:
        parts: list[str] = []

        async def collect(ev: object) -> None:
            if isinstance(ev, Text):
                parts.append(ev.content)

        await agent.run_turn(
            TurnRequest(
                origin=Origin.USER,
                source=Source(channel="host-e2e", chat_id="modes", sender_id="user",
                              chat_type=ChatType.DM),
                text=message,
                conversation="modes",
            ),
            collect,
            lambda: [],
            stream=False,
        )
        return "".join(parts)

    return _turn


def host_supports_auto() -> bool:
    """Whether this checkout has the `context_segments` plugin slot at all.

    Only an *absent symbol* answers no. A `ModuleNotFoundError` for something
    else is an interpreter missing Raven's dependencies, and reporting that as
    "stock Raven, auto not supported" would record a setup mistake as a host
    limitation — so it is raised instead.
    """
    import inspect

    from raven.agent.loop import AgentLoop  # deps missing here is the caller's problem

    try:
        from raven.cli._plugin_stack import build_plugin_segments  # noqa: F401
    except ImportError as err:
        if err.name and not err.name.startswith("raven"):
            raise
        return False
    return "plugin_segments" in inspect.signature(AgentLoop.__init__).parameters


def run(mode: str, skills: Path, workspace: Path, model: dict, prompt: str,
        timeout_s: float, *, broken_source: bool = False) -> dict:
    from raven.agent.loop import AgentLoop
    from raven.cli._plugin_stack import build_plugin_registry, build_plugin_tools
    from raven.config import RavenConfig
    from raven.providers.litellm_provider import LiteLLMProvider
    from raven.session.manager import SessionManager

    provider = LiteLLMProvider(
        api_key=model["api_key"], api_base=model["base_url"],
        default_model=f"openai/{model['model']}", extra_body=_e2e.thinking_off(),
    )
    cfg = RavenConfig()
    cfg.plugins.config["skillsearch"] = {
        "mode": mode,
        "skills_dir": str(skills),
        # Every remote catalogue off — see the note in `e2e_hermes.py`.
        # Under `--broken-source` the first one points at a closed port
        # instead, which is case P5.
        **_e2e.source_endpoints(broken=broken_source),
        "top_k": 1,
        # `build_plugin_tools` has no skillsearch special case, so the tool
        # half only gets a model channel if the config slice carries one.
        # `auto` keeps the host's live provider; `on_demand` deliberately does
        # not get one, because that is the path the plugin's own endpoint
        # settings exist to cover and the case has to exercise it.
        **({"_provider": provider} if mode == "auto" else {}),
        # Two different clients read this key, and they want different
        # spellings. Through `_provider` it reaches LiteLLM, which needs the
        # `openai/` route prefix. Without one the plugin builds its own
        # OpenAI-compatible client, which sends the string as the model id —
        # and a prefixed id there comes back
        # `404 The model 'openai/...' does not exist`, silently degrading the
        # rewriter to the raw query.
        "model": f"openai/{model['model']}" if mode == "auto" else model["model"],
        "model_base_url": model["base_url"],
        "model_api_key": model["api_key"],
    }
    registry = build_plugin_registry(cfg)
    tools = build_plugin_tools(workspace, cfg, registry=registry)

    segments = {}
    if mode == "auto":
        from raven.cli._plugin_stack import build_plugin_segments

        segments = build_plugin_segments(
            workspace, cfg, registry=registry,
            model=f"openai/{model['model']}", provider=provider,
        )

    trace: dict = {"tool_calls": [], "segment_builds": []}

    registered = []
    for tool in tools:
        if getattr(tool, "name", "") != "skill_search":
            registered.append(tool)
            continue
        inner = tool.execute

        async def execute(_inner=inner, **kwargs):
            out = await _inner(**kwargs)
            trace["tool_calls"].append({"args": kwargs, "returned": out})
            return out

        tool.execute = execute
        # The plugin's factory subclasses the host's own `Tool`, so the
        # inherited half — `to_schema`, `cast_params`, `validate_params`,
        # `display_call` — comes along. A duck-typed tool raises
        # `AttributeError: no attribute 'to_schema'` on the first turn, so
        # asserting it here keeps the case honest about which side was fixed.
        from raven.agent.tools.base import Tool as _HostTool

        assert isinstance(tool, _HostTool), "the plugin must return the host's Tool"
        registered.append(tool)

    seg = segments.get("skills")
    if seg is not None:
        inner_build = seg.build

        async def build(ctx, _inner=inner_build):
            out = await _inner(ctx)
            trace["segment_builds"].append(getattr(out, "text", None) or "")
            return out

        seg.build = build

    kwargs = {"plugin_segments": segments} if segments else {}
    agent = AgentLoop(
        provider=provider, workspace=workspace, model=f"openai/{model['model']}",
        max_iterations=8, session_manager=SessionManager(workspace),
        interactive=False, plugin_tools=registered, **kwargs,
    )
    # Raven's own always-on skills would answer nothing here, but leaving them
    # in makes a PASS ambiguous about where the text came from.
    agent.context.skills.get_always_skills = lambda: []
    agent.context.skills.build_skills_summary = lambda only=None: ""
    agent.context.skills.load_skills_for_context = lambda *a, **kw: ""

    started = time.time()
    reply = asyncio.run(asyncio.wait_for(_turn_runner()(agent, prompt), timeout_s))
    return {
        "mode": mode,
        "tool_offered": any(getattr(t, "name", "") == "skill_search" for t in tools),
        "tool_names": [getattr(t, "name", "?") for t in tools],
        "segment_registered": "skills" in segments,
        "segment_text": "\n".join(trace["segment_builds"]),
        "tool_calls": trace["tool_calls"],
        "reply": reply,
        "elapsed_s": round(time.time() - started, 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("modes", nargs="*", default=["auto", "on_demand"])
    ap.add_argument("--host", type=Path,
                    default=os.environ.get("SKILLSEARCH_E2E_RAVEN_CHECKOUT"),
                    help="a Raven checkout (the directory holding the `raven` package)")
    ap.add_argument("--plugin-site", default=os.environ.get("SKILLSEARCH_E2E_RAVEN_SITE"),
                    help="site-packages where skillsearch-raven is installed; added with "
                         "addsitedir so the `raven.plugins` entry point registers")
    ap.add_argument("--timeout", type=float, default=600.0)
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
        ap.error("--host or SKILLSEARCH_E2E_RAVEN_CHECKOUT is required")

    sys.path.insert(0, str(args.host))
    if args.plugin_site:
        # `addsitedir`, not `sys.path.insert`: the entry point only registers
        # if the `.pth` finders run, and this lands after the host's own
        # packages so nothing here shadows a host dependency.
        site.addsitedir(args.plugin_site)

    model = _e2e.model_config()
    supports_auto = host_supports_auto()
    print(f"host={args.host} model={model['model']}")
    print(f"context_segments slot: {'present' if supports_auto else 'ABSENT (stock Raven)'}")

    results, failures = {}, []
    for mode in args.modes:
        if mode == "auto" and not supports_auto:
            print("  auto       BLOCKED — this checkout has no `context_segments` "
                  "plugin slot; auto cannot run here")
            results[mode] = {"mode": mode, "result": "BLOCKED",
                             "reason": "host has no context_segments slot"}
            continue
        workspace = Path(tempfile.mkdtemp(prefix=f"raven-ws-{mode}-"))
        # Inside the workspace on purpose: a corpus in a stray temp directory
        # reads to the agent as a skill that is not installed — it checks, does
        # not find the directory, and says so. That is the harness lying about
        # the deployment, not retrieval failing.
        skills = _e2e.corpus(workspace)
        try:
            out = run(mode, skills, workspace, model,
                      _e2e.CASES[args.case]["prompt"], args.timeout,
                      broken_source=args.broken_source)
        except Exception as err:  # a host that will not start is the finding
            print(f"  {mode:10} host failed: {type(err).__name__}: {err}")
            results[mode] = {"mode": mode, "error": f"{type(err).__name__}: {err}"}
            failures.append(mode)
            continue
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

        delivered = "\n".join(
            [out["segment_text"]] + [c["returned"] for c in out["tool_calls"]]
        )
        ok, facts = _e2e.verdict(
            case=args.case,
            mode=mode,
            tool_offered=out["tool_offered"],
            auto_channel_filled=bool(out["segment_text"]),
            tool_called=bool(out["tool_calls"]),
            delivered=delivered,
            reply=out["reply"],
        )
        out["segment_registered_in_host"] = out["segment_registered"]
        out["verdict"] = {"pass": ok, **facts}
        results[mode] = out
        print(_e2e.line(mode, ok, facts, out["elapsed_s"]))
        print(f"             reply: {out['reply'][:280]!r}")
        if not ok:
            failures.append(mode)

    if args.dump:
        args.dump.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print("all passed" if not failures else f"failed: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
