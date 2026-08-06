"""Step 1: Collect skills from a skill pool directory into a single JSONL.

Reads every SKILL.md under the given directory, parses frontmatter
(name, description) and body, writes a flat JSONL for downstream steps.

Usage:
    python3 collect_skills.py \
        --skills_dir /path/to/skill_pool/skills \
        --output data/skills.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from pathlib import Path

import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

_FM_RE = re.compile(r"^---\s*\n(.*?\n)---\s*\n", re.DOTALL)

CATEGORY_MAP = {
    "agent-coordination": "agent", "ai-llm": "ai", "analysis-methods": "analysis",
    "analysis": "analysis", "applied": "applied", "artifact-generation": "generation",
    "business-monetization": "business", "business-productivity": "business",
    "c-level": "leadership", "context-management": "infra", "core": "core",
    "data": "data", "design-principles": "design", "design-ui": "design",
    "development": "development", "devops": "devops", "engineering": "engineering",
    "evaluation": "evaluation", "frameworks": "frameworks", "governance": "governance",
    "infrastructure": "infrastructure", "integration": "integration",
    "meta": "meta", "monitoring": "monitoring", "optimization": "optimization",
    "quality": "quality", "research": "research", "security": "security",
    "testing": "testing", "workflow": "workflow",
}


def parse_skill_md(path: Path) -> dict | None:
    try:
        text = path.read_text("utf-8", errors="replace")
    except Exception:
        return None

    fm = {}
    body = text
    m = _FM_RE.match(text)
    if m:
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            fm = {}
        body = text[m.end():]

    name = fm.get("name", path.parent.name)
    desc = fm.get("description", "")
    if isinstance(desc, dict):
        desc = str(desc)
    if not body.strip():
        return None

    return {"name": name, "description": desc, "body": body.strip()}


def infer_category(rel_path: str) -> str:
    top = rel_path.split("/")[0] if "/" in rel_path else ""
    for prefix, cat in CATEGORY_MAP.items():
        if top.startswith(prefix):
            return cat
    return "other"


def body_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skills_dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min_body_len", type=int, default=50,
                        help="Minimum body length in chars to keep a skill")
    args = parser.parse_args()

    if not args.skills_dir.exists():
        log.error("Skills directory not found: %s", args.skills_dir)
        sys.exit(1)

    args.output.parent.mkdir(parents=True, exist_ok=True)

    # BFS: once a directory contains SKILL.md, don't recurse into its children.
    # This avoids collecting reference/example skills nested under a parent skill.
    skill_files = []
    for dirpath, dirnames, filenames in os.walk(args.skills_dir, topdown=True):
        if "SKILL.md" in filenames:
            skill_files.append(Path(dirpath) / "SKILL.md")
            dirnames.clear()
    skill_files.sort()
    log.info("Found %d SKILL.md files (BFS, pruned at first SKILL.md)", len(skill_files))

    seen_hashes = set()
    skills = []

    for skill_md in skill_files:
        parsed = parse_skill_md(skill_md)
        if parsed is None:
            continue
        if len(parsed["body"]) < args.min_body_len:
            continue

        bh = body_hash(parsed["body"])
        if bh in seen_hashes:
            continue
        seen_hashes.add(bh)

        rel = str(skill_md.parent.relative_to(args.skills_dir))
        skill_id = rel.replace("/", "__")
        category = infer_category(rel)

        skills.append({
            "skill_id": skill_id,
            "name": parsed["name"],
            "description": parsed["description"],
            "body": parsed["body"],
            "category": category,
            "rel_path": rel,
        })

    with open(args.output, "w") as f:
        for s in skills:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    log.info("Collected %d unique skills (deduped from %d SKILL.md files)",
             len(skills), len(seen_hashes))
    log.info("Saved to %s", args.output)

    cats = {}
    for s in skills:
        cats[s["category"]] = cats.get(s["category"], 0) + 1
    log.info("Category distribution: %s", json.dumps(cats, indent=2))


if __name__ == "__main__":
    main()
