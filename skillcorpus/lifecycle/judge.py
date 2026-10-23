# SPDX-License-Identifier: MIT
"""LLM judge: score a finished agent workspace against a task's two hidden rubrics.

run_arms.py runs the agent and then calls judge_workspace(); this module can also judge workspaces produced by
another agent framework. For each run the judge sees (1) the task, (2) one rubric checklist, (3) the files in the agent's
workspace (collect_workspace), (4) the solver trajectory (render_trajectory) and (5) the agent's final reply, and
returns a score per checklist item. task_completion and instruction_following are judged in two separate calls.
The paper's judge is Qwen3.6-27B with thinking disabled.

build_prompt() reproduces the prompt the solver received in the unaided arm (task only) and the aided arm (task
plus the SKILL.md bodies; bundled skill files are placed under ./skills/<skill>/ in the workspace).

Run layout expected by the CLI (one case directory per task run):
  <cases-root>/<task_id>/workspace/        the agent's working directory after the run (inputs/ + deliverables)
  <cases-root>/<task_id>/reply.txt         optional: the agent's final chat reply
  <cases-root>/<task_id>/trajectory.jsonl  optional: OpenAI-format chat messages of the run (role / content /
                                           tool_calls); otherwise workspace/sessions/*/.jsonl is used if present
Outputs per case: judge_task_completion.json, judge_instruction_following.json (raw reply + item detail) and
result.json {"task_completion": {score, max, normalized, detail}, "instruction_following": {...}}.

Environment: JUDGE_BASE_URL, JUDGE_API_KEY (required), JUDGE_MODEL (default "Qwen3.6-27B"),
             JUDGE_EXTRA (JSON extra_body; default disables thinking via chat_template_kwargs),
             JUDGE_SKIP_LAKE=1 also hides Lean's .lake build cache from the judge.
Usage: python -m skillcorpus.lifecycle.judge --tasks-root tasks --cases-root runs/aided [--tasks t1 t2 ...] [--force]
Cases that already have a scored result.json are skipped unless --force is given; failed ones are judged again.
"""
from __future__ import annotations

import argparse
import asyncio
import codecs
import glob
import json
import os
import sys
from pathlib import Path

from ._layout import NO_THINK, reasoning_text, reply_json, skill_md

# Directories never shown to the judge: the installed skills, agent-framework state, caches and dependency trees.
# Add the state directory of your own agent framework with --skip-dir.
SKIP_DIRS = {"skills", "sessions", "__pycache__", ".git", "node_modules",
             ".skill_assets", "memory", "user_memory"}
# Optional: also hide Lean's dependency/build cache (.lake), which can hold tens of thousands of files.
if os.environ.get("JUDGE_SKIP_LAKE") == "1":
    SKIP_DIRS.add(".lake")


# ── solver prompt (reference) ───────────────────────────────────────

def load_task(task_dir: Path, skill_dirs=()):
    """Read task.md and, for the aided arm, the SKILL.md of each given skill directory."""
    task_md = (task_dir / "task.md").read_text(encoding="utf-8")
    skills = []
    for s in skill_dirs:
        body = skill_md(Path(s))
        if body is not None:
            skills.append((Path(s).name, body))
    return task_md, skills


def build_prompt(task_md: str, skills: list[tuple[str, str]]) -> str:
    parts = [
        "You are completing a real-world work task inside the current working "
        "directory. Input files (if any) are under ./inputs/. Write every "
        "deliverable exactly where the task says (paths are relative to the "
        "working directory). When finished, reply with a short summary.",
        "# Task\n\n" + task_md,
    ]
    if skills:
        body = "\n\n---\n\n".join(f"### Skill: {n}\n\n{b}" for n, b in skills)
        parts.append(
            "# Active Skills\n\nThe following skills encode the required "
            "workflow and standards for this task. Follow their instructions "
            "closely. Their bundled resource files are installed under "
            "./skills/<skill-name>/.\n\n" + body)
    return "\n\n---\n\n".join(parts)


# ── judge side ──────────────────────────────────────────────────────

def _xlsx_to_text(path: Path, max_rows: int = 120) -> str:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=False)
        out = []
        for ws in wb.worksheets:
            out.append(f"### sheet: {ws.title}")
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= max_rows:
                    out.append("... (truncated)")
                    break
                cells = ["" if c is None else str(c) for c in row]
                if any(cells):
                    out.append(" | ".join(cells))
        return "\n".join(out)
    except Exception as e:
        return f"(xlsx unreadable: {e})"


def collect_workspace(ws: Path, char_budget: int = 90_000) -> str:
    entries = []
    for f in sorted(ws.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(ws)
        if rel.parts and rel.parts[0] in SKIP_DIRS:
            continue
        is_input = rel.parts and rel.parts[0] == "inputs"
        cap = 2_000 if is_input else 20_000
        ext = f.suffix.lower()
        if ext == ".xlsx":
            body = _xlsx_to_text(f)[:cap]
        else:
            # Any file that decodes as UTF-8 is shown to the judge as text (.graphql/.liquid/extension-less
            # scripts etc. can be deliverables); only real binaries are replaced by a placeholder note.
            # The check reads the first 8192 bytes; a multi-byte character cut at that boundary is not an error.
            try:
                raw = f.read_bytes()
                codecs.getincrementaldecoder("utf-8")().decode(raw[:8192], final=False)
                body = raw.decode("utf-8", errors="replace")[:cap]
            except (UnicodeDecodeError, OSError):
                body = f"(binary, {f.stat().st_size} bytes)"
        entries.append(f"===== FILE: {rel} =====\n{body}\n")
    blob = "\n".join(entries)
    return blob[:char_budget]


def render_trajectory(ws: Path, char_budget: int = 45_000, path: Path | None = None) -> str:
    """Render a session log of OpenAI-format chat messages as a compact chronological action log (the judge sees
    the solver logs, as in the Tessl protocol). `path` defaults to the first workspace/sessions/*/.jsonl."""
    files = [str(path)] if path and Path(path).is_file() else glob.glob(str(ws / "sessions" / "*" / ".jsonl"))
    if not files:
        return "(no trajectory recorded)"
    lines, step = [], 0
    for raw in open(files[0], encoding="utf-8", errors="replace"):
        try:
            ev = json.loads(raw)
        except Exception:
            continue
        role = ev.get("role")
        if role == "assistant":
            step += 1
            txt = (ev.get("content") or "").strip()
            if txt:
                lines.append(f"[turn {step}] assistant: {txt[:600]}")
            for tc in ev.get("tool_calls") or []:
                fn = tc.get("function", {})
                lines.append(f"[turn {step}] tool_call {fn.get('name')}"
                             f"({str(fn.get('arguments'))[:350]})")
        elif role == "tool":
            lines.append(f"    -> result: {str(ev.get('content'))[:350]}")
    blob = "\n".join(lines)
    if len(blob) > char_budget:
        blob = blob[:15_000] + "\n... (middle truncated) ...\n" + blob[-(char_budget - 15_000):]
    return blob


JUDGE_SYS = (
    "You are a strict evaluation judge. You will see (1) the original task, "
    "(2) a scoring checklist, (3) the files the agent produced in its "
    "workspace, (4) the solver trajectory (chronological log of the agent's "
    "tool calls and results), and (5) the agent's final chat reply. Judge "
    "PROCESS items (prescribed workflow, tool usage, ordering, communication) "
    "from the trajectory. Score EVERY checklist "
    "item from 0 to its max_score. File-deliverable items must be judged "
    "from the files (missing file => 0); process/communication items (e.g. "
    "notifying the user, explaining decisions) may be judged from the final "
    "reply. Reply with a JSON array only: "
    '[{"name": "<item name>", "score": <number>}, ...] — no other text.'
)

JUDGE_EXTRA = json.loads(os.environ["JUDGE_EXTRA"]) if os.environ.get("JUDGE_EXTRA") else NO_THINK


def _is_score_list(v) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(d, dict) and "name" in d and "score" in d for d in v)


async def judge_rubric(provider, model: str, task_md: str, rubric: dict,
                       ws_blob: str, log_path: Path, reply: str = "", extra_body: dict | None = None):
    checklist = rubric.get("checklist") or rubric.get("items") or []
    if not checklist:
        return None
    def _key(it, i):
        return str(it.get("name") or it.get("id") or f"item{i}")
    items_txt = "\n".join(
        f"- name: {_key(it, i)} (max_score={it.get('max_score', 10)})\n"
        f"  criterion: {it.get('description', '')}"
        for i, it in enumerate(checklist))
    user = (f"# Task given to the agent\n{task_md}\n\n"
            f"# Rubric context\n{rubric.get('context', '')}\n\n"
            f"# Checklist\n{items_txt}\n\n"
            f"# Agent workspace files\n{ws_blob}\n\n"
            f"# Agent final reply to the user\n{reply[:6000]}")
    error = "JUDGE PARSE FAILURE"
    for _ in range(3):
        try:
            resp = await provider.chat.completions.create(
                model=model, max_tokens=4000, temperature=0.0,
                messages=[{"role": "system", "content": JUDGE_SYS},
                          {"role": "user", "content": user}],
                extra_body=JUDGE_EXTRA if extra_body is None else extra_body)
        except Exception as e:  # endpoint error: retry, and record it if every attempt fails
            error = f"{type(e).__name__}: {str(e)[:300]}"
            continue
        message = resp.choices[0].message
        text = message.content or reasoning_text(message)
        try:
            items = reply_json(message, _is_score_list, opener="[")
            scores = {d["name"]: float(d["score"]) for d in items}
        except (ValueError, TypeError):
            continue
        got, mx = 0.0, 0.0
        detail = []
        for i, it in enumerate(checklist):
            k = _key(it, i)
            cap = float(it.get("max_score", 10))
            s = max(0.0, min(cap, scores.get(k, 0.0)))
            got += s
            mx += cap
            detail.append({"name": k, "score": s, "max": cap})
        log_path.write_text(json.dumps({"raw": text, "detail": detail}, indent=1))
        return {"score": got, "max": mx, "normalized": got / mx if mx else None,
                "detail": detail}
    log_path.write_text(json.dumps({"raw": error}))
    return {"score": None, "max": None, "normalized": None, "detail": []}


async def judge_workspace(provider, model: str, task_dir: Path, ws: Path, case: Path,
                          reply: str = "", trajectory: Path | None = None) -> dict:
    """Score one finished workspace against both rubrics of task_dir; writes judge_*.json and result.json to case."""
    case.mkdir(parents=True, exist_ok=True)
    task_md, _ = load_task(task_dir)
    ws_blob = collect_workspace(ws)
    ws_blob += ("\n\n# Solver trajectory (action log)\n" + render_trajectory(ws, path=trajectory))
    result = {"task": task_dir.name, "reply_tail": reply[-500:]}
    for key, fname in (("task_completion", "task-completion.json"),
                       ("instruction_following", "instruction-following.json")):
        rp = task_dir / "rubrics" / fname
        if rp.exists():
            rubric = json.loads(rp.read_text(encoding="utf-8"))
            result[key] = await judge_rubric(
                provider, model, task_md, rubric, ws_blob,
                case / f"judge_{key}.json", reply=reply)
    (case / "result.json").write_text(json.dumps(result, indent=1))
    return result


def is_scored(case: Path) -> bool:
    """Whether a case has a result.json whose task-completion rubric was scored."""
    rp = case / "result.json"
    if not rp.is_file():
        return False
    try:
        r = json.loads(rp.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return (r.get("task_completion") or {}).get("score") is not None


async def amain(argv=None):
    ap = argparse.ArgumentParser(description="Judge finished agent workspaces against the two hidden rubrics.")
    ap.add_argument("--tasks-root", required=True, help="directory containing tasks/<task_id>/ (task.md, rubrics/)")
    ap.add_argument("--cases-root", required=True, help="directory containing <task_id>/workspace/ per run")
    ap.add_argument("--tasks", nargs="+", help="task ids to judge (default: every case directory with a workspace/)")
    ap.add_argument("--skip-dir", action="append", default=[], help="extra top-level workspace directory to hide from the judge")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--force", action="store_true", help="re-judge cases that already have result.json")
    args = ap.parse_args(argv)
    base, key = os.environ.get("JUDGE_BASE_URL"), os.environ.get("JUDGE_API_KEY")
    if not base or not key:
        sys.exit("JUDGE_BASE_URL and JUDGE_API_KEY must be set")
    model = os.environ.get("JUDGE_MODEL") or "Qwen3.6-27B"
    SKIP_DIRS.update(args.skip_dir)

    from openai import AsyncOpenAI
    # Thinking stays off (JUDGE_EXTRA): with thinking on, reasoning can use up the token budget and leave the JSON
    # scores empty
    provider = AsyncOpenAI(api_key=key, base_url=base)
    troot, croot = Path(args.tasks_root), Path(args.cases_root)
    names = args.tasks or sorted(p.name for p in croot.iterdir() if (p / "workspace").is_dir())
    sem = asyncio.Semaphore(args.concurrency)
    results = []

    async def worker(name):
        case = croot / name
        if is_scored(case) and not args.force:
            print(f"SKIP {name} (done)", flush=True)
            return
        td = troot / name
        if not td.is_dir() or not (case / "workspace").is_dir():
            print(f"MISSING {name}", flush=True)
            return
        reply = (case / "reply.txt").read_text(encoding="utf-8", errors="replace") if (case / "reply.txt").exists() else ""
        traj = case / "trajectory.jsonl"
        async with sem:
            try:
                r = await judge_workspace(provider, model, td, case / "workspace", case, reply=reply,
                                          trajectory=traj if traj.exists() else None)
            except Exception as e:
                print(f"FATAL {name}: {type(e).__name__}: {e}", flush=True)
                return
        results.append(r)
        tc = (r.get("task_completion") or {}).get("normalized")
        iff = (r.get("instruction_following") or {}).get("normalized")
        print(f"DONE {name} tc={tc if tc is None else round(tc, 3)} "
              f"if={iff if iff is None else round(iff, 3)}", flush=True)

    await asyncio.gather(*[worker(n) for n in names])
    tcs = [r["task_completion"]["normalized"] for r in results
           if (r.get("task_completion") or {}).get("normalized") is not None]
    ifs = [r["instruction_following"]["normalized"] for r in results
           if (r.get("instruction_following") or {}).get("normalized") is not None]
    print(f"[judge] {len(results)} judged  avg task_completion={sum(tcs)/len(tcs) if tcs else None} "
          f"avg instruction_following={sum(ifs)/len(ifs) if ifs else None}", flush=True)


def main(argv=None):
    asyncio.run(amain(argv))


if __name__ == "__main__":
    main()
