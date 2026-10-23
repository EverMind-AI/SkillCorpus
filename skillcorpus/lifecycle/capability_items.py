# SPDX-License-Identifier: MIT
"""Capability-item adjudicator: marks each task-completion rubric item of a task as a capability item or not.

The criterion asks the text-containment question directly, without abstracting knowledge points and without
referring to any particular solver model: "does passing this check require information that is stated in this
SKILL.md?" It is a pure (check, skill document) relation. The paper uses Qwen3.5-397B with thinking disabled; an item
is a capability item when the adjudicator returns needs_doc = true for it (items missing from its reply are not).

Inputs:
  --tasks-root  tasks/<task_id>/rubrics/task-completion.json
  --skills-root skills/<skill>/SKILL.md, where <skill> is the task id without the "tessl-single-" prefix and the
                trailing "_<idx>"
Output: one JSONL line per task, {"task_id": ..., "skill": ..., "capability": {<item name>: true|false}}, or
{"task_id": ..., "skill": ..., "error": ...} when the adjudicator gave no usable reply. Tasks already classified in
the output file are skipped and failed ones are retried, so the script can be resumed.

Environment: ADJ_BASE_URL, ADJ_API_KEY (required), ADJ_MODEL (default "Qwen3.5-397B"),
             ADJ_EXTRA (JSON extra_body; default disables thinking via chat_template_kwargs).
Usage: python -m skillcorpus.lifecycle.capability_items --tasks-root tasks --skills-root skills -o capability_items.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics as st
import sys
from pathlib import Path

from ._layout import NO_THINK, is_dict_with, reply_json, skill_md, skill_of

SYS = ("You are given a SKILL document and a list of evaluation checks from a task built for that skill. "
 "For EACH check decide: does passing this check require information that is STATED IN THE DOCUMENT?\n"
 "Answer yes if the check tests a specific API/function name, an exact parameter or threshold, a mapping or "
 "transformation rule, a required procedure or ordering, or an opinionated convention that the document "
 "specifies — something a competent engineer would NOT reliably produce without reading this document.\n"
 "Answer no if the check tests generic software practice, file existence, output format/validity, code "
 "compiling, or anything a competent engineer would do anyway.\n"
 "Judge the check against the DOCUMENT's content only. Do not speculate about what any particular model knows.\n"
 'Reply ONLY JSON: {"v":[{"check":"<check name verbatim>","needs_doc":true|false}]}')


async def ask(cli, model: str, extra: dict, sem: asyncio.Semaphore, user: str) -> dict[str, bool] | None:
    """{check name: needs_doc} from the adjudicator, or None after three failed attempts."""
    async with sem:
        for _ in range(3):
            try:
                r = await cli.chat.completions.create(
                    model=model, temperature=0.0, max_tokens=3000, extra_body=extra,
                    messages=[{"role": "system", "content": SYS}, {"role": "user", "content": user}])
                j = reply_json(r.choices[0].message, is_dict_with("v"))
                if j.get("v"):
                    return {str(x.get("check")): bool(x.get("needs_doc")) for x in j["v"] if x.get("check")}
            except Exception:
                continue
    return None


async def classify(cli, model: str, extra: dict, sem: asyncio.Semaphore, task_id: str, skill: str, md: str,
                   checks: list[dict]) -> dict:
    rec = {"task_id": task_id, "skill": skill}
    checklist = "\n".join(f"- {c['name']}: {c['desc'][:200]}" for c in checks)
    user = f"===== SKILL.md =====\n{md[:14000]}\n\n===== Checks =====\n{checklist}"
    try:
        labels = await ask(cli, model, extra, sem, user)
    except Exception as e:
        labels, rec["error"] = None, f"{type(e).__name__}: {str(e)[:90]}"
    if labels:
        rec["capability"] = labels
    else:
        rec.setdefault("error", "no usable adjudicator reply")
    return rec


def read_done(path: Path) -> set[str]:
    """Task ids that already have a non-empty classification in the output file."""
    done = set()
    if path.exists():
        for line in open(path, encoding="utf-8"):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("capability"):
                done.add(rec["task_id"])
    return done


async def amain(argv=None):
    ap = argparse.ArgumentParser(description="Mark task-completion rubric items as capability items.")
    ap.add_argument("--tasks-root", required=True, help="directory containing tasks/<task_id>/")
    ap.add_argument("--skills-root", required=True, help="directory containing skills/<skill>/SKILL.md")
    ap.add_argument("--task-file", help="optional file with one task id per line (default: all tasks)")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("-o", "--out", required=True, help="output JSONL (appended; classified task ids are skipped)")
    a = ap.parse_args(argv)
    base, key = os.environ.get("ADJ_BASE_URL"), os.environ.get("ADJ_API_KEY")
    if not base or not key:
        sys.exit("ADJ_BASE_URL and ADJ_API_KEY must be set")
    model = os.environ.get("ADJ_MODEL") or "Qwen3.5-397B"
    extra = json.loads(os.environ["ADJ_EXTRA"]) if os.environ.get("ADJ_EXTRA") else NO_THINK

    troot, sroot, out = Path(a.tasks_root), Path(a.skills_root), Path(a.out)
    if a.task_file:
        names = [line.strip() for line in open(a.task_file, encoding="utf-8") if line.strip()]
    else:
        names = sorted(p.name for p in troot.iterdir() if p.is_dir())
    done = read_done(out)
    todo = []
    for t in names:
        rp, s = troot / t / "rubrics" / "task-completion.json", skill_of(t)
        md = skill_md(sroot / s)
        if t in done or not rp.is_file() or md is None:
            continue
        rubric = json.loads(rp.read_text(encoding="utf-8"))
        checks = [{"name": c.get("name"), "desc": str(c.get("description", ""))}
                  for c in rubric.get("checklist", []) if c.get("name")]
        todo.append((t, s, md, checks))
    print(f"{len(todo)} tasks to classify", flush=True)

    from openai import AsyncOpenAI
    cli = AsyncOpenAI(base_url=base, api_key=key, timeout=240.0, max_retries=2)
    sem = asyncio.Semaphore(a.concurrency)
    n = 0
    with open(out, "a", encoding="utf-8") as f:
        for fut in asyncio.as_completed([classify(cli, model, extra, sem, *x) for x in todo]):
            f.write(json.dumps(await fut, ensure_ascii=False) + "\n")
            f.flush()
            n += 1
            if n % 20 == 0:
                print(f"  {n}/{len(todo)}", flush=True)
    ok = [r["capability"] for r in map(json.loads, open(out, encoding="utf-8")) if r.get("capability")]
    if ok:
        share = st.mean(v for r in ok for v in r.values())
        print(f"  {len(ok)} tasks classified, share of items judged needs_doc {share:.0%}", flush=True)


def main(argv=None):
    asyncio.run(amain(argv))


if __name__ == "__main__":
    main()
