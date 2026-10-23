#!/usr/bin/env python3
"""Step 1: synthesize one training request per skill with an LLM.

A stratified sample of skills (proportional to category sizes) is drawn from the training pool and an
LLM writes, for each, a realistic task request that needs the skill without naming it, at temperature
0.8 (jittered by +-0.1 per call); requests of 30 characters or fewer are dropped.

Input pool: JSONL with "pid" (or "skill_id"), "name", "description", "body" and optionally "category".
Output: JSONL in the requests format, {"task", "query", "gt_pids"}, appended as results arrive so an
interrupted run resumes with --resume.

Example:
  python train/synthesize_requests.py --pool data/train_pool.jsonl --out data/train_requests.jsonl \
      --api <chat completions URL> --model <model id> --exclude data/test_pids.json
"""
import argparse
import json
import os
import random
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import chat, load_jsonl, skill_id  # noqa: E402

SYSTEM_PROMPT = ("You are an experienced user of AI assistants. "
                 "You write clear, realistic task requests that describe what you need to accomplish.")
USER_TEMPLATE = """Given the following skill specification, write a realistic task description that someone would ask an AI assistant to help with. The task should naturally require the capabilities described in this skill.

Skill name: {name}
Category: {category}
Description: {description}
Skill body: {body_preview}

Requirements: (1) Describe a concrete scenario with specific inputs/outputs. (2) Include enough detail that the skill would be clearly useful. (3) Do NOT mention the skill name "{name}" anywhere in the task.

Output ONLY the task description."""


def build_prompt(skill, body_max=2000):
    return USER_TEMPLATE.format(name=skill.get("name", ""), category=skill.get("category", "unknown"),
                                description=(skill.get("description") or "")[:500],
                                body_preview=(skill.get("body") or "")[:body_max])


def stratified_sample(skills, n):
    """Each category contributes in proportion to its size (at least one skill)."""
    by_cat = defaultdict(list)
    for s in skills:
        by_cat[s.get("category", "other")].append(s)
    sampled, remainder = [], []
    for cat_skills in by_cat.values():
        quota = max(1, round(len(cat_skills) / len(skills) * n))
        random.shuffle(cat_skills)
        sampled.extend(cat_skills[:quota])
        remainder.extend(cat_skills[quota:])
    if len(sampled) > n:
        random.shuffle(sampled)
        sampled = sampled[:n]
    elif len(sampled) < n:
        random.shuffle(remainder)
        sampled.extend(remainder[:n - len(sampled)])
    print(f"stratified sample: {len(sampled)} skills from {len(by_cat)} categories", flush=True)
    return sampled


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True, help="training skill pool (JSONL)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--api", required=True, help="OpenAI-compatible chat completions URL")
    ap.add_argument("--api_key", default=os.environ.get("LLM_API_KEY"))
    ap.add_argument("--model", required=True)
    ap.add_argument("--max_skills", type=int, default=38000)
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--exclude", help="JSON list of skill ids to leave out (e.g. benchmark gold skills)")
    ap.add_argument("--disable_thinking", action="store_true", help="for reasoning models served by vLLM")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    random.seed(a.seed)

    skills = load_jsonl(a.pool)
    if a.exclude:
        with open(a.exclude) as f:
            excluded = {str(x) for x in json.load(f)}
        skills = [s for s in skills if skill_id(s) not in excluded]
    if 0 < a.max_skills < len(skills):
        skills = stratified_sample(skills, a.max_skills)  # seeded, so a resumed run draws the same sample
    if a.resume and os.path.exists(a.out):
        done = {str(r["gt_pids"][0]) for r in load_jsonl(a.out)}
        skills = [s for s in skills if skill_id(s) not in done]
    print(f"requests to write: {len(skills)}", flush=True)

    extra = {"chat_template_kwargs": {"enable_thinking": False}} if a.disable_thinking else None
    lock, n_ok, n_fail = threading.Lock(), 0, 0
    with open(a.out, "a" if a.resume else "w", encoding="utf-8") as fout, \
            ThreadPoolExecutor(max_workers=a.workers) as ex:
        futures = {}
        for s in skills:
            temp = min(1.5, max(0.1, a.temperature + random.uniform(-0.1, 0.1)))
            messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": build_prompt(s)}]
            futures[ex.submit(chat, a.api, a.model, messages, temp, 1024, a.api_key, extra)] = s
        for fu in as_completed(futures):
            s, text = futures[fu], fu.result()
            with lock:
                if text and len(text) > 30:
                    sid = skill_id(s)
                    fout.write(json.dumps({"task": f"{sid}__q0", "query": text, "gt_pids": [sid]},
                                          ensure_ascii=False) + "\n")
                    fout.flush()
                    n_ok += 1
                else:
                    n_fail += 1
    print(f"written {n_ok}, failed {n_fail} -> {a.out}")


if __name__ == "__main__":
    main()
