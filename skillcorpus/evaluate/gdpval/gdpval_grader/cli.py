"""Command-line grader.

    python -m gdpval_grader --workspace ./deliverables --task-id 0a1b2c3d
    python -m gdpval_grader --workspace ./deliverables --task-json task.json

The API key is read from --api-key or GDPVAL_API_KEY / OPENROUTER_API_KEY /
OPENAI_API_KEY. To judge with OpenAI directly instead of OpenRouter:
    --base-url https://api.openai.com/v1 --model gpt-4o
"""

import argparse
import json
import logging
import sys

from .evaluator import DEFAULT_BASE_URL, DEFAULT_MODEL, grade_workspace
from .tasks import load_gdpval_task, load_task_from_json


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gdpval_grader",
        description="Grade GDPval deliverables in a workspace against a task rubric "
                    "using an LLM judge.")
    p.add_argument("--workspace", required=True,
                   help="Directory containing the deliverable files to grade.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--task-id",
                     help="GDPval task id (full or 8-char) — loads rubric from "
                          "the openai/gdpval HuggingFace dataset.")
    src.add_argument("--task-json",
                     help="Path to a JSON file holding the task dict "
                          "(needs at least 'occupation' and 'rubric_json').")
    p.add_argument("--model", default=DEFAULT_MODEL,
                   help=f"Judge model (default: {DEFAULT_MODEL}).")
    p.add_argument("--base-url", default=None,
                   help=f"OpenAI-compatible base URL (default: {DEFAULT_BASE_URL}).")
    p.add_argument("--api-key", default=None,
                   help="API key (else GDPVAL_API_KEY / OPENROUTER_API_KEY / "
                        "OPENAI_API_KEY).")
    p.add_argument("--meta-prompts-dir", default="",
                   help="Override the bundled per-occupation meta-prompts "
                        "(fallback rubric source).")
    p.add_argument("--out", default=None,
                   help="Write the full result JSON here (default: stdout).")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Log judge progress to stderr.")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s", stream=sys.stderr)

    task = (load_gdpval_task(args.task_id) if args.task_id
            else load_task_from_json(args.task_json))

    result = grade_workspace(
        task, args.workspace,
        api_key=args.api_key, model=args.model, base_url=args.base_url,
        meta_prompts_dir=args.meta_prompts_dir)

    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(payload)

    # Always print a one-line summary to stdout; full JSON too if not written out.
    tid = task.get("task_id") or args.task_json
    if result.get("error"):
        print(f"[{tid}] reward={result['reward']}  ERROR={result['error']}")
    else:
        print(f"[{tid}] reward={result['reward']}  "
              f"files={result.get('files_found')}")
    if not args.out:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
