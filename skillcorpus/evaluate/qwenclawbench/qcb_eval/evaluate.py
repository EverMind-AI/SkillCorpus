#!/usr/bin/env python3
"""QwenClawBench evaluator.

Grades pre-produced agent results (workspace + transcript per task) with the
benchmark's own automated checks and LLM judge, then aggregates to a score.

It does NOT run any agent — you bring the results. See RESULTS_FORMAT.md.

Input (a results directory):
    <results_dir>/
        <task_id>/
            workspace/          # the agent's final workspace (files it produced)
            transcript.json     # list of {"role","content",...} messages
    # (transcript may instead live as grading.json["transcript"] — both accepted)

Usage:
    export JUDGE_API_KEY=...            # for the LLM judge (hybrid tasks)
    export JUDGE_BASE_URL=https://openrouter.ai/api/v1
    python -m qcb_eval.evaluate \
        --tasks-dir data/qwenclawbench-v1.1-100/tasks \
        --results-dir path/to/agent_results \
        --output scores.json
    # auto-only (no judge / no API key needed):
    python -m qcb_eval.evaluate ... --no-judge
"""
from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .tasks import TaskLoader
from .grading import grade_task, _grade_automated, DEFAULT_JUDGE_MODEL

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("qcb_eval")


def _load_transcript(task_dir: Path) -> list:
    """Read the agent transcript from transcript.json or grading.json['transcript']."""
    tj = task_dir / "transcript.json"
    if tj.exists():
        try:
            return json.loads(tj.read_text())
        except Exception:
            pass
    gj = task_dir / "grading.json"
    if gj.exists():
        try:
            return json.loads(gj.read_text()).get("transcript", []) or []
        except Exception:
            pass
    return []


def _build_execution_result(task_dir: Path) -> dict:
    ws = task_dir / "workspace"
    return {
        "transcript": _load_transcript(task_dir),
        "workspace": str(ws) if ws.exists() else "",
        "status": "completed",
    }


def _grade_one(task, results_dir: Path, skill_dir: Path, judge_model: str, no_judge: bool):
    task_dir = results_dir / task.task_id
    if not task_dir.exists():
        return task.task_id, None, "no result dir"
    exec_result = _build_execution_result(task_dir)
    try:
        if no_judge:
            gr = _grade_automated(task, exec_result)
        else:
            gr = grade_task(task=task, execution_result=exec_result,
                            skill_dir=skill_dir, judge_model=judge_model)
        return task.task_id, gr, None
    except Exception as e:
        return task.task_id, None, str(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks-dir", required=True, help="dir of benchmark task *.md files")
    ap.add_argument("--results-dir", required=True, help="dir of per-task agent results")
    ap.add_argument("--output", default="scores.json")
    ap.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--no-judge", action="store_true",
                    help="automated checks only (skip LLM judge; hybrid tasks scored on auto)")
    ap.add_argument("--suite", default="all", help="'all' or comma-separated task_ids")
    args = ap.parse_args()

    tasks_dir = Path(args.tasks_dir).resolve()
    results_dir = Path(args.results_dir).resolve()
    skill_dir = tasks_dir.parent  # for asset resolution if a grade() needs it

    tasks = TaskLoader(tasks_dir).load_all_tasks()
    if args.suite != "all":
        want = {t.strip() for t in args.suite.split(",") if t.strip()}
        tasks = [t for t in tasks if t.task_id in want]
    log.info("Loaded %d tasks; grading against %s (judge=%s)",
             len(tasks), results_dir, "OFF" if args.no_judge else args.judge_model)

    results = {}
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = {ex.submit(_grade_one, t, results_dir, skill_dir, args.judge_model, args.no_judge): t
                for t in tasks}
        for fut in as_completed(futs):
            tid, gr, err = fut.result()
            if gr is None:
                log.warning("  %s: FAILED (%s)", tid, err)
                results[tid] = {"score": 0.0, "error": err}
            else:
                results[tid] = {
                    "score": round(gr.score, 4),
                    "grading_type": gr.grading_type,
                    "breakdown": gr.breakdown,
                    "notes": gr.notes,
                }
                log.info("  %s: %.3f (%s)", tid, gr.score, gr.grading_type)

    scores = [r["score"] for r in results.values()]
    summary = {
        "n_tasks": len(tasks),
        "n_graded": sum(1 for r in results.values() if "error" not in r),
        "mean_score": round(statistics.mean(scores), 4) if scores else 0.0,
        "judge_model": None if args.no_judge else args.judge_model,
        "results": results,
    }
    Path(args.output).write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    log.info("DONE — %d/%d graded, mean_score=%.4f → %s",
             summary["n_graded"], summary["n_tasks"], summary["mean_score"], args.output)


if __name__ == "__main__":
    main()
