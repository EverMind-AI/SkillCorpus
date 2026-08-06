"""Step 2: Generate synthetic (query, skill) pairs using an LLM.

For each skill, prompts an LLM to produce a realistic task description
that would require that skill — without revealing the skill name.

The reference recipe uses GPT-4o-mini and generates ~38K pairs via
stratified sampling across 51 categories; any OpenAI-compatible
endpoint/model works.

Usage:
    python3 generate_queries.py \
        --skills data/skills.jsonl \
        --output data/queries.jsonl \
        --api_base http://localhost:8000/v1 \
        --model gpt-4o-mini \
        --num_queries_per_skill 1 \
        --max_workers 16
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from openai import OpenAI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are an experienced user of AI assistants. "
    "You write clear, realistic task requests that describe what you need to accomplish."
)

USER_TEMPLATE = """Given the following skill specification, write a realistic task description that someone would ask an AI assistant to help with. The task should naturally require the capabilities described in this skill.

Skill name: {name}
Category: {category}
Description: {description}
Skill body: {body_preview}

Requirements: (1) Describe a concrete scenario with specific inputs/outputs. (2) Include enough detail that the skill would be clearly useful. (3) Do NOT mention the skill name "{name}" anywhere in the task.

Output ONLY the task description."""


def build_prompt(skill: dict, body_max: int = 2000) -> str:
    return USER_TEMPLATE.format(
        name=skill.get("name", ""),
        category=skill.get("category", "unknown"),
        description=(skill.get("description") or "")[:500],
        body_preview=(skill.get("body") or "")[:body_max],
    )


def call_llm(client: OpenAI, model: str, skill: dict, temperature: float,
             disable_thinking: bool = False) -> str | None:
    try:
        kwargs = {}
        if disable_thinking:
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_prompt(skill)},
            ],
            temperature=temperature,
            max_tokens=1024,
            **kwargs,
        )
        content = resp.choices[0].message.content
        return content.strip() if content else None
    except Exception as e:
        log.warning("LLM call failed for skill %s: %s", skill.get("skill_id", "?"), e)
        return None


def stratified_sample(skills: list[dict], n: int, stratified: bool) -> list[dict]:
    """Sample n skills, optionally stratified by category (paper approach).

    Paper: "stratified sampling across 51 categories" — each category
    contributes proportionally to its size in the full pool.
    """
    if not stratified:
        random.shuffle(skills)
        return skills[:n]

    by_cat = defaultdict(list)
    for s in skills:
        by_cat[s.get("category", "other")].append(s)

    total = len(skills)
    sampled = []
    remainder = []

    for cat, cat_skills in by_cat.items():
        quota = max(1, round(len(cat_skills) / total * n))
        random.shuffle(cat_skills)
        sampled.extend(cat_skills[:quota])
        remainder.extend(cat_skills[quota:])

    if len(sampled) > n:
        random.shuffle(sampled)
        sampled = sampled[:n]
    elif len(sampled) < n:
        random.shuffle(remainder)
        sampled.extend(remainder[:n - len(sampled)])

    log.info("Stratified sample: %d skills from %d categories", len(sampled), len(by_cat))
    return sampled


def load_existing(path: Path) -> set[str]:
    if not path.exists():
        return set()
    seen = set()
    with open(path) as f:
        for line in f:
            if line.strip():
                d = json.loads(line)
                seen.add(d["skill_id"])
    return seen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skills", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--api_base", type=str, required=True,
                        help="OpenAI-compatible API base URL")
    parser.add_argument("--api_key", type=str, default="EMPTY")
    parser.add_argument("--model", type=str, default="gpt-4o-mini")
    parser.add_argument("--num_queries_per_skill", type=int, default=1)
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--max_workers", type=int, default=16)
    parser.add_argument("--max_skills", type=int, default=38000,
                        help="Limit number of skills to sample (paper uses ~38K from ~80K pool)")
    parser.add_argument("--stratified", action="store_true", default=True,
                        help="Stratified sampling across categories (paper approach)")
    parser.add_argument("--exclude_ids", type=Path, default=None,
                        help="JSON file with skill IDs to exclude (e.g., eval benchmark skills)")
    parser.add_argument("--resume", action="store_true",
                        help="Skip skills already present in output file")
    parser.add_argument("--disable_thinking", action="store_true",
                        help="Disable thinking mode for Qwen3.5/thinking models")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    skills = []
    with open(args.skills) as f:
        for line in f:
            if line.strip():
                skills.append(json.loads(line))
    log.info("Loaded %d skills", len(skills))

    exclude_ids = set()
    if args.exclude_ids and args.exclude_ids.exists():
        exclude_ids = set(json.loads(args.exclude_ids.read_text()))
        log.info("Excluding %d eval skill IDs", len(exclude_ids))

    skills = [s for s in skills if s["skill_id"] not in exclude_ids]
    log.info("After exclusion: %d skills", len(skills))

    if args.resume:
        existing = load_existing(args.output)
        skills = [s for s in skills if s["skill_id"] not in existing]
        log.info("After resume filter: %d skills remaining", len(skills))

    if args.max_skills > 0 and args.max_skills < len(skills):
        skills = stratified_sample(skills, args.max_skills, args.stratified)
    log.info("Will generate queries for %d skills", len(skills))

    client = OpenAI(base_url=args.api_base, api_key=args.api_key, timeout=120)

    mode = "a" if args.resume else "w"
    out_f = open(args.output, mode)
    done = 0
    failed = 0
    t0 = time.time()

    tasks_to_do = []
    for skill in skills:
        for qi in range(args.num_queries_per_skill):
            tasks_to_do.append((skill, qi))

    log.info("Total LLM calls: %d", len(tasks_to_do))

    with ThreadPoolExecutor(max_workers=args.max_workers) as pool:
        futures = {}
        for skill, qi in tasks_to_do:
            temp = args.temperature + random.uniform(-0.1, 0.1)
            temp = max(0.1, min(1.5, temp))
            fut = pool.submit(call_llm, client, args.model, skill, temp,
                             args.disable_thinking)
            futures[fut] = (skill, qi)

        for fut in as_completed(futures):
            skill, qi = futures[fut]
            query_text = fut.result()
            if query_text and len(query_text) > 30:
                record = {
                    "query_id": f"{skill['skill_id']}__q{qi}",
                    "skill_id": skill["skill_id"],
                    "skill_name": skill["name"],
                    "category": skill.get("category", ""),
                    "instruction_text": query_text,
                }
                out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
                out_f.flush()
                done += 1
            else:
                failed += 1

            total_processed = done + failed
            if total_processed % 100 == 0 or total_processed <= 10:
                elapsed = time.time() - t0
                rate = total_processed / elapsed if elapsed > 0 else 0
                eta_h = (len(tasks_to_do) - total_processed) / rate / 3600 if rate > 0 else 0
                log.info("Progress: %d/%d done=%d failed=%d %.2f/s ETA=%.1fh",
                         total_processed, len(tasks_to_do), done, failed, rate, eta_h)

    out_f.close()
    elapsed = time.time() - t0
    log.info("Finished: %d queries generated, %d failed, %.1fs total",
             done, failed, elapsed)
    log.info("Saved to %s", args.output)


if __name__ == "__main__":
    main()
