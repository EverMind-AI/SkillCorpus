# SPDX-License-Identifier: MIT
"""Run an agent on lifecycle tasks in the unaided or the aided arm, then judge each run.

Each task runs in its own workspace. The unaided arm gives the agent the task only. The aided arm adds the SKILL.md
text of the task's skill to the prompt and copies the skill directory to ``./skills/<skill>/`` in the workspace
(``judge.build_prompt``). The agent is Raven's single-agent tool-use loop with shell access to the workspace, at most
``--max-turns`` turns, and runtime skill retrieval switched off, which is the setting of the paper. Raven is a
separate project and is not shipped here: point ``--raven-root`` (or ``RAVEN_ROOT``) at a checkout, as for the
SkillsBench runner in ``skillcorpus/evaluate/skillsbench``.

After the run the workspace is scored against both hidden rubrics by ``judge.judge_workspace`` unless ``--no-judge``
is given. Output per task (``<out>/<task_id>/``): ``workspace/``, ``reply.txt``, ``run.json`` (elapsed time, error)
and, when judged, ``result.json`` with the item scores. ``labels.py`` reads these files. A task whose run finished
but was not scored is judged again on the next invocation without rerunning the agent.

The agent executes shell commands as the calling user and is not confined to its workspace. Run it inside a
container or a virtual machine, or at least with a separate HOME.

Usage:
  python -m skillcorpus.lifecycle.run_arms --tasks-root data/tasks --skills-root data/skills \\
      --arm aided --model Qwen3.6-27B --api-base http://host:8000/v1 --raven-root ../raven \\
      -o runs/qwen36/aided/r1 [--tasks t1 t2 | --task-file list.txt] [--concurrency 4]

Environment: SOLVER_API_KEY for the agent's endpoint, and JUDGE_BASE_URL / JUDGE_API_KEY / JUDGE_MODEL for the judge
(see judge.py). ``--key-stdin`` reads one key from the first line of standard input and uses it for both.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

from . import judge as judge_mod
from ._layout import skill_of

ARMS = ("unaided", "aided")


def prepare_workspace(task_dir: Path, ws: Path, arm: str, skills_root: Path | None) -> tuple[str, list]:
    """Create the workspace of one run and return (prompt, [(skill name, SKILL.md text)])."""
    if arm not in ARMS:
        raise ValueError(f"arm must be one of {ARMS}, got {arm!r}")
    ws.mkdir(parents=True, exist_ok=True)
    inp = task_dir / "inputs"
    if inp.is_dir():
        shutil.copytree(inp, ws / "inputs", dirs_exist_ok=True)
    skill_dirs = []
    if arm == "aided":
        if skills_root is None:
            raise ValueError("the aided arm needs --skills-root")
        sdir = skills_root / skill_of(task_dir.name)
        if not sdir.is_dir():
            raise FileNotFoundError(f"skill directory not found: {sdir}")
        shutil.copytree(sdir, ws / "skills" / sdir.name, dirs_exist_ok=True)
        skill_dirs = [sdir]
    task_md, skills = judge_mod.load_task(task_dir, skill_dirs)
    if arm == "aided" and not skills:
        raise FileNotFoundError(f"no SKILL.md in {skill_dirs[0]}: the aided arm needs the skill text")
    return judge_mod.build_prompt(task_md, skills), skills


def build_raven_agent(raven_root: Path, workspace: Path, model: str, api_base: str, api_key: str, max_turns: int):
    """Raven's AgentLoop with spawn disabled and every runtime skill lane switched off (the skill, if any, is in the
    prompt and in ./skills/)."""
    if str(raven_root) not in sys.path:
        sys.path.insert(0, str(raven_root))
    from raven.agent.loop import AgentLoop
    from raven.providers.litellm_provider import LiteLLMProvider
    from raven.session.manager import SessionManager

    provider = LiteLLMProvider(api_key=api_key, api_base=api_base or None, default_model=model)
    agent = AgentLoop(
        provider=provider, workspace=workspace, model=model,
        max_iterations=max_turns, context_window_tokens=128000,
        restrict_to_workspace=False, session_manager=SessionManager(workspace),
        interactive=False,
        # single-turn benchmark mode has no spine submit path, so sub-agent results could not be returned
        disabled_tools=["spawn"],
    )
    agent.context.skills.get_always_skills = lambda: []
    agent.context.skills.build_skills_summary = lambda only=None: ""
    agent.context.skills.load_skills_for_context = lambda *a, **kw: ""
    return agent


async def run_turn(agent_loop, message: str, *, session_key: str, chat_id: str) -> str:
    """One user turn through the Raven spine; returns the concatenated text of the reply."""
    from raven.spine import ChatType, Origin, Source, Text, TurnRequest
    parts: list[str] = []

    async def _collect(ev: object) -> None:
        if isinstance(ev, Text):
            parts.append(ev.content)

    await agent_loop.run_turn(
        TurnRequest(origin=Origin.USER,
                    source=Source(channel="benchmark", chat_id=chat_id, sender_id="user", chat_type=ChatType.DM),
                    text=message, conversation=session_key),
        _collect, lambda: [], stream=False)
    return "".join(parts)


async def run_one(task_dir: Path, args, out_dir: Path, judge_client) -> dict | None:
    name = task_dir.name
    case = out_dir / name
    ran = (case / "run.json").is_file() and not args.force
    if ran and (args.no_judge or judge_mod.is_scored(case)):
        print(f"SKIP {name} (done)", flush=True)
        return None
    if ran:  # the agent finished but the run was not scored: judge the existing workspace
        run = json.loads((case / "run.json").read_text(encoding="utf-8"))
        reply = (case / "reply.txt").read_text(encoding="utf-8", errors="replace") if (case / "reply.txt").is_file() else ""
        r = await judge_mod.judge_workspace(judge_client, args.judge_model, task_dir, case / "workspace", case,
                                            reply=reply)
        run["task_completion"] = (r.get("task_completion") or {}).get("normalized")
        print(f"JUDGED {name} arm={args.arm} tc={run['task_completion']}", flush=True)
        return run
    if case.exists():
        shutil.rmtree(case)
    ws = case / "workspace"
    prompt, skills = prepare_workspace(task_dir, ws, args.arm, Path(args.skills_root) if args.skills_root else None)
    # An agent that cannot be built never ran: raise, so that no run.json or score is written for the task
    agent = build_raven_agent(Path(args.raven_root), ws, args.model, args.api_base, args.api_key, args.max_turns)
    start, err, reply = time.time(), None, ""
    try:
        reply = await asyncio.wait_for(run_turn(agent, prompt, session_key=f"lifecycle-{name}", chat_id=name),
                                       timeout=args.timeout)
    except asyncio.TimeoutError:
        err = "timeout"
    except Exception as e:  # recorded and judged anyway: a crashed run still leaves a workspace
        err = f"{type(e).__name__}: {e}"
    run = {"task": name, "arm": args.arm, "model": args.model, "n_skills": len(skills),
           "elapsed_s": round(time.time() - start, 1), "error": err}
    (case / "reply.txt").write_text(reply, encoding="utf-8")
    (case / "run.json").write_text(json.dumps(run, indent=1), encoding="utf-8")
    if not args.no_judge:
        r = await judge_mod.judge_workspace(judge_client, args.judge_model, task_dir, ws, case, reply=reply)
        run["task_completion"] = (r.get("task_completion") or {}).get("normalized")
    print(f"DONE {name} arm={args.arm} tc={run.get('task_completion')} {run['elapsed_s']}s"
          + (f" ERR: {err[:80]}" if err else ""), flush=True)
    return run


def _task_names(args) -> list[str]:
    names = list(args.tasks or [])
    if args.task_file:
        names += [l.strip() for l in open(args.task_file, encoding="utf-8") if l.strip()]
    if not names:
        names = sorted(p.name for p in Path(args.tasks_root).iterdir() if p.is_dir())
    return names


async def amain(argv=None):
    ap = argparse.ArgumentParser(description="Run one arm (unaided or aided) of the lifecycle tasks and judge it.")
    ap.add_argument("--tasks-root", required=True, help="directory with one sub-directory per task")
    ap.add_argument("--skills-root", help="directory with one sub-directory per skill (required for --arm aided)")
    ap.add_argument("--arm", choices=ARMS, required=True)
    ap.add_argument("--model", required=True, help="model name served at --api-base")
    ap.add_argument("--api-base", required=True, help="OpenAI-compatible endpoint of the agent's model")
    ap.add_argument("--raven-root", default=os.environ.get("RAVEN_ROOT"), help="path of a Raven checkout")
    ap.add_argument("--tasks", nargs="+", help="task ids (default: every task under --tasks-root)")
    ap.add_argument("--task-file", help="file with one task id per line")
    ap.add_argument("--max-turns", type=int, default=40)
    ap.add_argument("--timeout", type=int, default=1200, help="seconds per run")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--no-judge", action="store_true", help="run the agent only")
    ap.add_argument("--force", action="store_true", help="rerun tasks that already have output")
    ap.add_argument("--key-stdin", action="store_true", help="read one API key from stdin for solver and judge")
    ap.add_argument("-o", "--output-dir", required=True)
    args = ap.parse_args(argv)
    if not args.raven_root:
        sys.exit("--raven-root (or RAVEN_ROOT) is required")
    args.api_key = os.environ.get("SOLVER_API_KEY", "dummy")
    judge_key, judge_base = os.environ.get("JUDGE_API_KEY"), os.environ.get("JUDGE_BASE_URL")
    if args.key_stdin:
        key = sys.stdin.readline().strip()
        if not key:
            sys.exit("--key-stdin: standard input is empty")
        args.api_key = judge_key = key
    args.judge_model = os.environ.get("JUDGE_MODEL") or "Qwen3.6-27B"
    judge_client = None
    if not args.no_judge:
        if not (judge_base and judge_key):
            sys.exit("JUDGE_BASE_URL and JUDGE_API_KEY must be set (or pass --no-judge)")
        from openai import AsyncOpenAI
        judge_client = AsyncOpenAI(api_key=judge_key, base_url=judge_base)

    troot = Path(args.tasks_root)
    task_dirs = [troot / n for n in _task_names(args)]
    missing = [d for d in task_dirs if not d.is_dir()]
    if missing:
        sys.exit(f"task not found: {missing[0]}")
    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[run_arms] {len(task_dirs)} tasks, arm={args.arm}, model={args.model}, conc={args.concurrency}", flush=True)
    sem = asyncio.Semaphore(args.concurrency)

    async def worker(td):
        async with sem:
            try:
                return await run_one(td, args, out_dir, judge_client)
            except Exception as e:
                print(f"FATAL {td.name}: {type(e).__name__}: {e}", flush=True)
                return None

    runs = [r for r in await asyncio.gather(*[worker(td) for td in task_dirs]) if r]
    tcs = [r["task_completion"] for r in runs if r.get("task_completion") is not None]
    print(f"[run_arms] {len(runs)} runs, mean task_completion="
          f"{sum(tcs) / len(tcs) if tcs else None} -> {out_dir}", flush=True)


if __name__ == "__main__":
    asyncio.run(amain())
