"""Case P1 in both modes, through a real Hermes `AIAgent`.

The plugin's own suite drives its provider directly. This drives the host:
the plugin is copied into a throwaway `$HERMES_HOME/plugins/skillsearch` and
named in `config.yaml`, and Hermes's runtime is what loads it, calls
`initialize`, folds `get_tool_schemas()` into the tool surface and `prefetch()`
into the turn. What is asserted is what the model said, so a PASS means the
skill reached it — not that a factory returned something.

    auto       `prefetch` fills, `skill_search` is absent from the tool surface
    on_demand  the tool is offered, `prefetch` stays empty, and the model has
               to decide to call it

Usage:

    export SKILLSEARCH_E2E_BASE_URL=... SKILLSEARCH_E2E_MODEL=...
    python e2e_hermes.py --host /path/to/hermes-agent [auto|on_demand ...]

## Host limitation this script has to work around

Hermes caps an external provider's whole `prefetch` at 8 seconds, in the host,
whatever `timeout_s` the plugin config asks for. The rewriter alone is one
model call and against a reasoning deployment it spends more than that, so
`auto` under the host default delivers nothing and the failure looks like a
retrieval bug. `--prefetch-budget` raises the host's cap for the run and
`--no-rewrite` removes the call instead; the report has to state which was
used, because "auto passes" means something different in each case.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sys
import tempfile
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _e2e

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parents[2] / "plugin-hermes"
# The engine beside the plugin has to win over any `skillsearch` already
# installed in the interpreter, so it goes in front of everything.
sys.path.insert(0, str(HERE.parents[2] / "engine-python"))

CHAT_TIMEOUT_S = 600.0


def build_home(mode: str, skills: Path, model: dict, *, rewrite: bool) -> Path:
    """A throwaway `$HERMES_HOME` carrying the plugin and its settings."""
    home = Path(tempfile.mkdtemp(prefix=f"hermes-{mode}-"))
    (home / "plugins").mkdir(parents=True)
    (home / "logs").mkdir(parents=True)
    shutil.copytree(
        PLUGIN,
        home / "plugins" / "skillsearch",
        ignore=shutil.ignore_patterns("__pycache__", "tests"),
    )
    (home / "skillsearch.json").write_text(
        json.dumps(
            {
                "mode": mode,
                "rewrite": rewrite,
                "skills_dir": str(skills),
                # Every remote catalogue off. These cases measure the mode
                # switch, not what a public catalogue returned this minute —
                # and 0.2.0 onwards ships all three enabled by default, so
                # leaving them alone means testing against live network.
                "hub_endpoint": "",
                "clawhub_endpoint": "",
                "skillhub_cn_endpoint": "",
                "top_k": 1,
                "max_select": 1,
                "model": model["model"],
                "model_base_url": model["base_url"],
                "model_api_key": model["api_key"],
                "timeout_s": 300,
                "rewrite_timeout_s": 60,
                "gate_timeout_s": 120,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    # `$HERMES_HOME/config.yaml` is what `hermes_cli.config` reads. A
    # `config.json` beside it is never loaded and the provider then stays
    # silently unregistered, which looks like the plugin failing to load.
    (home / "config.yaml").write_text(
        "memory:\n  provider: skillsearch\nagent: {}\n", encoding="utf-8"
    )
    return home


def run(mode: str, skills: Path, host: Path, model: dict, *, prompt: str, rewrite: bool,
        prefetch_budget_s: float | None) -> dict:
    home = build_home(mode, skills, model, rewrite=rewrite)
    workspace = Path(tempfile.mkdtemp(prefix=f"hermes-ws-{mode}-"))
    out: dict = {"mode": mode, "rewrite": rewrite, "home": str(home)}
    cwd = os.getcwd()
    if str(host) not in sys.path:
        sys.path.insert(0, str(host))
    os.environ["HERMES_HOME"] = str(home)
    os.environ.setdefault("OPENROUTER_API_KEY", model["api_key"])
    started = time.time()
    try:
        os.chdir(workspace)
        from run_agent import AIAgent

        agent = AIAgent(
            base_url=model["base_url"], api_key=model["api_key"], model=model["model"],
            max_iterations=12, quiet_mode=True,
            skip_context_files=True, skip_memory=False,
        )
        try:
            manager = getattr(agent, "_memory_manager", None)
            providers = list(getattr(manager, "providers", []) or []) if manager else []
            out["providers"] = [p.name for p in providers]
            provider = next((p for p in providers if p.name == "skillsearch"), None)
            if provider is None:
                out["error"] = "skillsearch provider never registered"
                return out

            out["schemas"] = [s.get("name") for s in provider.get_tool_schemas()]
            out["tool_on_agent_surface"] = any(
                (t or {}).get("function", {}).get("name") == "skill_search"
                for t in (agent.tools or [])
                if isinstance(t, dict)
            )
            if prefetch_budget_s is not None:
                manager._external_prefetch_timeout = prefetch_budget_s
            out["prefetch_budget_s"] = manager._external_prefetch_timeout

            block = provider.prefetch(prompt)
            out["prefetch_len"] = len(block)
            # Kept whole, not truncated: this is one half of what the verdict
            # searches for the fixture facts, and a head-only copy would fail
            # the case whenever the facts sit past the cut.
            out["prefetch_text"] = block

            calls: list[dict] = []
            inner = provider.handle_tool_call

            def spy(tool_name, args, **kwargs):
                result = inner(tool_name, args, **kwargs)
                calls.append({"tool": tool_name, "args": args, "returned": result or ""})
                return result

            provider.handle_tool_call = spy  # type: ignore[method-assign]

            pool = ThreadPoolExecutor(max_workers=1)
            future = pool.submit(agent.chat, prompt)
            try:
                out["reply"] = future.result(timeout=CHAT_TIMEOUT_S) or ""
                out["status"] = "completed"
            except FuturesTimeout:
                out["reply"] = ""
                out["status"] = "timeout"
            finally:
                pool.shutdown(wait=False)
            out["tool_calls"] = calls
        finally:
            with contextlib.suppress(Exception):
                agent.close()
    except Exception:
        out["error"] = traceback.format_exc()
    finally:
        os.chdir(cwd)
        out["elapsed_s"] = round(time.time() - started, 1)
        shutil.rmtree(workspace, ignore_errors=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("modes", nargs="*", default=["auto", "on_demand"])
    ap.add_argument("--host", type=Path,
                    default=os.environ.get("SKILLSEARCH_E2E_HERMES_CHECKOUT"),
                    help="a hermes-agent checkout (the directory holding run_agent.py)")
    ap.add_argument("--prefetch-budget", type=float, default=None,
                    help="raise the host's 8s external-prefetch cap for this run")
    ap.add_argument("--no-rewrite", action="store_true",
                    help="drop the rewriter's model call from the prefetch path")
    ap.add_argument("--case", default="p1", choices=sorted(_e2e.CASES),
                    help="which case in cases.md to run; each swaps the prompt "
                         "and what the verdict requires")
    ap.add_argument("--dump", type=Path, default=None)
    args = ap.parse_args()
    if not args.host:
        ap.error("--host or SKILLSEARCH_E2E_HERMES_CHECKOUT is required")

    model = _e2e.model_config()
    skills = _e2e.corpus()
    print(f"host={args.host} model={model['model']} corpus={skills}")
    budget = args.prefetch_budget or "host default (8s)"
    print(f"rewrite={not args.no_rewrite} prefetch_budget={budget}")

    results, failures = {}, []
    for mode in args.modes:
        out = run(mode, skills, Path(args.host), model,
                  prompt=_e2e.CASES[args.case]["prompt"],
                  rewrite=not args.no_rewrite, prefetch_budget_s=args.prefetch_budget)
        results[mode] = out
        if "error" in out:
            print(f"  {mode:10} host failed:\n{out['error']}")
            failures.append(mode)
            continue
        delivered = "\n".join(
            [out["prefetch_text"]] + [c["returned"] for c in out["tool_calls"]]
        )
        ok, facts = _e2e.verdict(
            case=args.case,
            mode=mode,
            tool_offered=bool(out["schemas"]),
            auto_channel_filled=bool(out["prefetch_len"]),
            tool_called=bool(out["tool_calls"]),
            delivered=delivered,
            reply=out["reply"],
        )
        out["verdict"] = {"pass": ok, **facts}
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
