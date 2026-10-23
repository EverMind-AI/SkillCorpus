# SPDX-License-Identifier: MIT
"""Synthesize one evaluation task plus two hidden rubrics from each sampled skill.

This is the synthesis step that produced the tasks of the paper's dataset. The task format follows the
Tessl task-evals-for-skills layout ("A Framework for Evaluating Agentic Skills at Scale", arXiv 2606.17819); only
the skill -> task synthesis step is implemented here.

Skill source: a directory with one sub-directory per skill (SKILL.md plus bundled files, optionally SOURCE.json),
as stored in the paper's dataset under skills/<skill>/, optionally restricted by a CSV listing. Skills are
sampled with a fixed random seed.

Stages:
  1 environment: the solver environment is fixed (internet, python3, node/npm, git, ffmpeg, go, rustc, docker,
    sqlite3, pandoc, jq; no php/java/MCP/real databases other than sqlite/browser/multi-turn interaction/
    pre-existing repo state/API keys). The critic judges "can this be performed" against that list.
  2 generation: a generator of ANOTHER model family than the target model reads SKILL.md and writes task.md +
    inputs + two rubrics. The goal may be stated (the API/tool/domain the skill is about may be named); the
    conventions may not (library choice, parameters, naming, structure, required steps, prohibited patterns),
    because those are tested secretly by the instruction-following rubric.
  3 critic: leakage (conventions/answers in the task), completeness (all referenced files present, only
    capabilities the environment has), rubric hardness.
  4 difficulty gate: the target model attempts the task unaided (no skill); a judge scores the attempt against the
    task-completion rubric. If it passes more than DIFF_MAX of the items the task is regenerated; if it is still
    too easy after the retries the skill is dropped.

Output (the layout of the paper's dataset):
  <out>/tasks/<task_id>/task.md
  <out>/tasks/<task_id>/inputs/*
  <out>/tasks/<task_id>/rubrics/task-completion.json
  <out>/tasks/<task_id>/rubrics/instruction-following.json
  <out>/skills/<skill>/                    (copy of SKILL.md + bundled files)
  <out>/metadata.csv
  <out>/synth_results.jsonl                (one record per skill: ok / rejected / too_easy / error)

Usage:
  python -m skillcorpus.lifecycle.synthesize --skills-dir skills --sample 100 --seed 42 --out output
  CONC=8 python -m skillcorpus.lifecycle.synthesize --skills-dir skills --skills-csv list.csv --out output

Environment (endpoints are OpenAI-compatible; the defaults are the settings of the paper):
  GEN_BASE_URL, GEN_API_KEY (required), GEN_MODEL      generator (default "deepseek/deepseek-v4-flash")
  GEN_EXTRA       JSON extra_body of the generator (default switches reasoning off in the OpenRouter form)
  CRITIC_BASE_URL, CRITIC_API_KEY (required unless --no-validate), CRITIC_MODEL   critic (default "Qwen3.6-27B")
  TARGET_BASE_URL, TARGET_API_KEY, TARGET_MODEL        unaided solver and judge of the difficulty gate
                                                       (default: the critic's endpoint and model)
  CRITIC_EXTRA    JSON extra_body of the critic and difficulty-gate calls (default disables thinking)
  DIFF_MAX        a task is too easy when the unaided attempt passes more than this share of items (default 0.5)
  NO_DIFF_GATE=1  skip the difficulty gate
  CRITIC_MODE     "full" (default: leakage, completeness and hardness all block) or "completeness" (only missing
                  files and unavailable capabilities block; leakage is left to the difficulty gate)
  GEN_TRIES       generation attempts per task for the critic and for the difficulty gate (default 2)
  MAX_BODY        characters of SKILL.md shown to the generator and the critic (default 16000)
  CONC            skills processed concurrently (default 4)
  SAMPLE_SEED     default of --seed (default 42)
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import os
import random
import re
import shutil
import sys
import time
from pathlib import Path

from openai import AsyncOpenAI

from ._layout import NO_THINK, TASK_PREFIX, is_dict_with, msg_text, reply_json


def _env(name, default=None):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


# Critic (validation agent). The paper uses the target model, Qwen3.6-27B, as critic.
CRITIC_BASE_URL = _env("CRITIC_BASE_URL")
CRITIC_MODEL = _env("CRITIC_MODEL", "Qwen3.6-27B")
CRITIC_API_KEY = _env("CRITIC_API_KEY")
# Difficulty gate: the unaided solver and its judge (= the target model). Defaults to the critic endpoint/model,
# which is how the paper's tasks were produced (one model served as critic, unaided solver and difficulty judge).
TARGET_BASE_URL = _env("TARGET_BASE_URL", CRITIC_BASE_URL)
TARGET_MODEL = _env("TARGET_MODEL", CRITIC_MODEL)
TARGET_API_KEY = _env("TARGET_API_KEY", CRITIC_API_KEY)
# Adversarial generation: the generator is from another model family, which breaks the "generator = solver"
# coupling (otherwise any crux the generator can embed is one the solver can naturally do).
GEN_BASE_URL = _env("GEN_BASE_URL")
GEN_MODEL = _env("GEN_MODEL", "deepseek/deepseek-v4-flash")
GEN_API_KEY = _env("GEN_API_KEY")
# extra_body that switches the generator's reasoning off. The default is the OpenRouter form used for the
# paper's tasks; other providers need their own switch.
GEN_EXTRA = json.loads(_env("GEN_EXTRA", '{"reasoning": {"enabled": false}}'))
# Difficulty gate: unaided (no skill) pass rate > DIFF_MAX means too easy -> reject
DIFF_MAX = float(os.environ.get("DIFF_MAX", "0.5"))
# Critic mode: full = leakage + completeness + hardness are all critical; completeness = only completeness
# (leakage/difficulty are left to the measured difficulty gate)
CRITIC_MODE = os.environ.get("CRITIC_MODE", "full")
NO_DIFF_GATE = os.environ.get("NO_DIFF_GATE", "") == "1"
# extra_body of the critic and difficulty-gate calls (thinking disabled, vLLM-style chat_template_kwargs)
CRITIC_EXTRA = json.loads(_env("CRITIC_EXTRA")) if _env("CRITIC_EXTRA") else NO_THINK
CONC = int(os.environ.get("CONC", "4"))
MAX_BODY = int(os.environ.get("MAX_BODY", "16000"))
GEN_TRIES = int(os.environ.get("GEN_TRIES", "2"))
SAMPLE_SEED = int(os.environ.get("SAMPLE_SEED", "42"))


# ── prompts ────────────────────────────────────────────────────────

TASK_GEN_SYS = """\
You are the task-generation agent in a skill-evaluation pipeline. Given an agent SKILL
(a SKILL.md artifact plus supporting files), you synthesize ONE realistic, executable
evaluation task that exercises the skill, together with two hidden scoring rubrics.

Two things must hold:
1. GOAL not convention, and GOAL not answer. The task states the GOAL (what to produce /
   what problem to solve). It MAY name the tool / API / library / domain the skill is about,
   when that is intrinsic to the goal. It MUST NOT reveal the OPINIONATED CONVENTIONS the
   skill prescribes: which library to choose when alternatives exist, exact parameter values,
   naming rules, required output structure/sections, required steps/ordering, prohibited
   patterns. Those are tested SECRETLY by the instruction-following rubric.
2. No answer leakage for internalization measurement. Specifying your own deliverables is
   REQUIRED, not leakage: name the output file paths, say what artifact to produce, provide
   the inputs. What you must NOT reveal is the DOMAIN KNOWLEDGE the rubrics test — which
   specific API/function/library, which exact value/threshold/parameter, which mapping or
   transformation rule, which convention among valid alternatives, which required ordering. Prohibited anywhere in task_description or inputs:
   exact function/API signatures, exact parameter values or thresholds, exact file naming
   or structure that a rubric checks, parenthetical hints like \"(use merge mode)\",
   listed primitives or field names a rubric will check, sample or example values that
   reveal the expected output, ordering hints, or near-verbatim phrasing of a rubric item.
   When the goal inherently requires a specific value, present it as a requirement to
   DISCOVER or DERIVE, not as a given.
3. Solvable-without-skill. A capable agent can complete the GOAL without the skill, but
   only follows the conventions and produces the specific knowledge if it has the skill.
4. Transformation-task pattern. Design the task as a TRANSFORMATION or REPAIR over
   concrete inputs, not as a list of requirements. Give the solver input files or data
   (the CONTENT lives in inputs) and ask for the result (translate, migrate, fix, convert,
   generate). The skill's crux is the transformation knowledge (mappings, APIs, procedures),
   which the task never states because it is exactly what the skill provides. State only
   WHAT to produce at the level of the goal, never HOW at the level of the answer. When
   tempted to write a requirement that names a specific value, move that value into the
   inputs or delete it and let the rubric test it secretly.

Solver environment: internet access, python3, node/npm, git, ffmpeg, go, rustc, docker,
sqlite3, pandoc, jq. NOT available: MCP servers, real databases (other than sqlite), a
browser, php, java, pre-existing repos/state, or API keys.

Reply with ONLY a single valid JSON object (no markdown fences, no commentary) with
exactly this schema:
{
  "task_description": "<full markdown of task.md: goal, background/context, concrete
    deliverables with exact filenames, and concrete assumptions/figures needed. Specific
    and self-contained. Reference input files as inputs/<name> when you provide them.>",
  "inputs": [ {"path": "inputs/<filename>", "content": "<full file content>"} ],
  "task_completion_rubric": [
      {"name": "<kebab-case id>", "description": "<what must be true>",
       "max_score": <int 1..10>, "task_reference": "<verbatim phrase from task_description>"}
  ],
  "instruction_following_rubric": [
      {"name": "<kebab-case id>", "description": "<what must be true>",
       "max_score": <int 1..10>, "skill_reference": "<verbatim phrase from the SKILL.md>"}
  ]
}

Rules:
- task_completion_rubric: 6..15 items. The rubric must test the SKILL'S SPECIFIC TECHNICAL
  CRUX, not generic deliverables. Each item must check the exact knowledge only the skill
  provides: the specific API/function/algorithm, the exact mapping or transformation, the
  precise procedure. A model WITHOUT the skill should FAIL most of these items. Do NOT make
  items check generic outputs (file exists, function defined, output produced) unless the
  correct content of that output embodies the crux. A good test: rewrite each item as
  \"produces the skill-specific X exactly\" and drop the item if X is not skill-specific.
  task_reference must be a verbatim snippet from task_description.
- instruction_following_rubric: 6..15 items checking the solution FOLLOWS the opinionated
  preferences in the SKILL.md. skill_reference must be a verbatim-ish snippet from SKILL.md.
- max_score values within each rubric should sum to approximately 100.
- inputs: only if the task genuinely needs input files; provide full realistic content.
  Otherwise return an empty list.
- Do NOT mention the skill's name anywhere in task_description or inputs.
"""

VALIDATE_SYS = """\
You are the validation agent in a skill-evaluation pipeline. Check a generated task for
quality before release. Reply with ONLY a single JSON object:
{"pass": true/false, "critical": ["..."], "minor": ["..."]}.
Judge against these rules:
0. WHAT IS NOT LEAKAGE (read first): a task MUST specify its own deliverables. Output file
   paths and names, the container format of a deliverable (a markdown report, a JSON file, a
   shell script), the general shape of what to produce, and the inputs provided are TASK
   SPECIFICATION, not leakage — even when a rubric item checks that same file exists or that
   the deliverable is present. Do NOT flag those. Leakage is about DOMAIN KNOWLEDGE the solver
   should have to bring: which specific API/function/library, which exact value/threshold/
   parameter, which mapping or transformation rule, which naming or structural convention
   among valid alternatives, which required ordering of steps.
1. LEAKAGE (critical): the task_description or inputs reveal, hint, or let the solver infer
   the DOMAIN KNOWLEDGE that either rubric tests. This covers instruction-following
   conventions (library choice among alternatives, exact parameter values, naming/structure,
   required steps/ordering, prohibited patterns) AND task-completion answers (exact
   function/API signatures, thresholds, output file naming or structure a rubric checks,
   expected values). Also count as leakage: parenthetical hints like \"(use merge mode)\",
   listed primitives or field names a rubric checks, sample values revealing expected output,
   ordering hints, near-verbatim phrasing of a rubric item. Naming the tool/API/domain that
   is the skill's subject is the GOAL, not leakage, but naming the SPECIFIC mechanism or
   value a rubric checks IS leakage.
2. COMPLETENESS (critical): every input file the task references is present; the task only
   needs capabilities the solver has (internet, python3, node/npm, git, ffmpeg, go, rustc,
   docker, sqlite3, pandoc, jq). Flag if it needs an MCP server, a real database, browser
   automation, php, java, a pre-existing repo/git state, multi-turn interactive input, or
   an API key the solver would not have.
3. RUBRIC HARDNESS (critical): task_completion_rubric items must test the SKILL'S SPECIFIC
   TECHNICAL CRUX (exact APIs, algorithms, mappings, procedures only the skill provides).
   Flag as critical if most TC items only check generic deliverables (file exists, function
   defined, output produced) or if a model without the skill would pass most items by
   general competence alone. A valid TC rubric should be failed by a no-skill model on
   most items.
4. RUBRIC QUALITY (minor): rubric items are concrete and checkable, references match their
   source, max_scores are positive and sum near 100.
Set pass=false if any critical issue exists.
"""


BARE_SOLVE_SYS = """\
You are an expert engineer. Complete the task below using ONLY your own general knowledge.
You have no playbook, no skill document, no reference material. Produce the concrete
deliverable: the exact code, commands, file contents, or values you would write. Be specific
and complete. Do not ask questions, do not hedge, do not describe what you would do — do it.
"""

DIFF_JUDGE_SYS = """\
Given a task-completion rubric and a solution produced WITHOUT the reference skill, judge for
EACH rubric item whether the solution satisfies it. Be strict: the solution must contain the
exact required value/API/format, not an approximation or a promise.
Reply with ONLY a single JSON object: {"passed": ["<exact rubric item name>", ...]}.
"""


def slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s or "skill"


def skill_identity(source: str, name: str) -> str:
    """Tessl naming {creator}_{repo}_{skill}. A source containing '/' is owner/repo, otherwise a bare creator.
    Used for skills that come from a CSV row with `source` and `name` but whose directory name is not already the
    identifier; skills read from a skills/ directory keep their directory name, which is the identifier."""
    src = (source or "").strip()
    if "/" in src:
        owner, repo = src.split("/", 1)
        return f"{slug(owner)}_{slug(repo)}_{slug(name)}"
    return f"{slug(src)}_{slug(name)}"


def source_url(source: str) -> str:
    return source if source.startswith("http") else (f"https://github.com/{source}" if source else "")


# ── skill loading (directory of skills, seeded sampling, optional categories) ──

def _frontmatter(text: str) -> dict:
    """Minimal YAML front-matter reader for the `name` and `description` keys (single-line values only)."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.S)
    out = {}
    if m:
        for line in m.group(1).splitlines():
            k = re.match(r"^(name|description)\s*:\s*(.*)$", line)
            if k and k.group(2).strip() not in ("", "|", ">", "|-", ">-"):
                out[k.group(1)] = k.group(2).strip().strip("\"'")
    return out


def _source_of(skill_dir: Path) -> str:
    """owner/repo from SOURCE.json (the dataset's format), else the raw value, else ''."""
    p = skill_dir / "SOURCE.json"
    if not p.is_file():
        return ""
    try:
        sj = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return ""
    src = str(sj.get("source_repo") or sj.get("source") or "")
    m = re.match(r"https?://github\.com/([^/]+/[^/#?]+?)(?:\.git)?/?$", src)
    return m.group(1) if m else src


def load_skills(skills_dir: Path, csv_path=None, id_from_source=False) -> list:
    """One record per skill directory. By default the directory name is both the skill id and its Tessl-style
    identifier ({creator}_{repo}_{skill}), used in the task id tessl-single-<skill>_<idx>; with id_from_source the
    identifier is rebuilt from source + name by skill_identity().
    With --skills-csv, only the listed skills are loaded; columns: `skill` (sub-directory name under --skills-dir)
    or `path` (a skill directory or its SKILL.md); optional `name`, `source`, `description` override the values
    read from SKILL.md front matter / SOURCE.json."""
    entries = []
    if csv_path:
        for r in csv.DictReader(open(csv_path, newline="", encoding="utf-8")):
            p = r.get("path") or r.get("skill") or ""
            d = Path(p) if r.get("path") else skills_dir / p
            if d.name == "SKILL.md":
                d = d.parent
            entries.append((d, r))
    else:
        entries = [(d, {}) for d in sorted(skills_dir.iterdir()) if (d / "SKILL.md").is_file()]
    rows = []
    for d, over in entries:
        md = d / "SKILL.md"
        if not md.is_file():
            continue
        body = md.read_text(encoding="utf-8", errors="ignore")
        fm = _frontmatter(body)
        row = {"id": d.name, "skill_unique": d.name,
               "name": over.get("name") or fm.get("name") or d.name,
               "source": over.get("source") or _source_of(d),
               "description": over.get("description") or fm.get("description") or "",
               "body": body, "path": str(md)}
        if id_from_source:
            row["skill_unique"] = skill_identity(row["source"], row["name"])
        rows.append(row)
    return rows


def load_categories(path):
    """Optional JSONL with {"id": <skill dir name>, "type": <category>, "sub": <sub-category>} per line.
    Returns (type_by_id, sub_by_id); empty dicts if no file is given."""
    type_by_id, sub_by_id = {}, {}
    if path:
        for line in open(path, encoding="utf-8"):
            d = json.loads(line)
            if d.get("type"):
                type_by_id[d["id"]] = d["type"]
            if d.get("sub") not in (None, ""):
                sub_by_id[d["id"]] = d["sub"]
    return type_by_id, sub_by_id


def iter_skills(skills, limit, sample, exclude_ids, seed, type_by_id, sub_by_id):
    """Sample from all skills (sample>0: random sample with a fixed seed; limit>0: the first N by id).
    Skills in exclude_ids are left out (to avoid repeating earlier batches). When a category file is given, only
    categorised skills are eligible. Each row carries type (category) and sub."""
    by_id = {r["id"]: r for r in skills}
    universe = set(type_by_id) & set(by_id) if type_by_id else set(by_id)
    all_ids = sorted(universe - set(exclude_ids))
    if sample:
        random.seed(seed)
        chosen = random.sample(all_ids, min(sample, len(all_ids)))
    elif limit:
        chosen = all_ids[:limit]
    else:
        chosen = all_ids
    rows = []
    for i in chosen:
        r = by_id[i]
        if not (r["body"] or "").strip():
            continue
        rows.append(dict(r, type=type_by_id.get(i, ""), sub=sub_by_id.get(i, "")))
    rows.sort(key=lambda r: r["id"])
    return rows


def package_skill(row, dst_dir: Path) -> bool:
    """Copy the skill's SKILL.md (+ bundled files) to the output skills/ directory; whole directory if path is valid."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    p = row["path"]
    if p and os.path.isfile(p):
        src = Path(p).parent
        if src.resolve() == dst_dir.resolve():
            return True
        for item in src.iterdir():
            if item.name.startswith("."):
                continue
            tgt = dst_dir / item.name
            if item.is_dir():
                shutil.copytree(item, tgt, dirs_exist_ok=True)
            else:
                shutil.copy2(item, tgt)
        (dst_dir / "SKILL.md").write_text(row["body"], encoding="utf-8")
        return True
    (dst_dir / "SKILL.md").write_text(row["body"], encoding="utf-8")
    return False


# ── generation + validation ────────────────────────────────────────

async def generate(cli, row, feedback=None) -> dict:
    user = (f"# Skill: {row['name']}\n"
            f"source: {row['source']}\n"
            f"description: {row['description'] or ''}\n\n"
            f"===== SKILL.md =====\n{(row['body'] or '')[:MAX_BODY]}")
    if feedback:
        user += ("\n\n===== PREVIOUS ATTEMPT WAS REJECTED =====\n"
                 + "\n".join(f"- {x}" for x in feedback[:4])
                 + "\n\nRewrite the task so none of these apply. If an item was leaked, either move "
                   "that content into an input file the solver must read, or remove it and let the "
                   "rubric test it secretly. Do not restate rubric requirements in the task.")
    for _ in range(3):
        resp = await cli.chat.completions.create(
            model=GEN_MODEL, temperature=0.7, max_tokens=8000,
            extra_body=GEN_EXTRA,
            messages=[{"role": "system", "content": TASK_GEN_SYS},
                      {"role": "user", "content": user}])
        try:
            spec = reply_json(resp.choices[0].message, is_dict_with("task_description"))
            assert isinstance(spec.get("task_description"), str)
            assert isinstance(spec.get("task_completion_rubric"), list)
            assert isinstance(spec.get("instruction_following_rubric"), list)
            # Structural pre-check: too few rubric items (the generator often puts everything into IF) -> retry locally
            assert len(spec["task_completion_rubric"]) >= 6, "TC rubric too few"
            assert len(spec["instruction_following_rubric"]) >= 6, "IF rubric too few"
            return spec
        except Exception:
            continue
    raise RuntimeError("generation: JSON parsing failed (3 attempts)")


async def validate(cli, row, spec) -> tuple[bool, list, list]:
    user = (f"# Skill\n{(row['body'] or '')[:MAX_BODY]}\n\n"
            f"# Generated task_description\n{spec.get('task_description', '')}\n\n"
            f"# Generated inputs\n{json.dumps(spec.get('inputs', []), ensure_ascii=False)[:4000]}\n\n"
            f"# instruction_following_rubric\n"
            f"{json.dumps(spec.get('instruction_following_rubric', []), ensure_ascii=False)}")
    for _ in range(3):
        resp = await cli.chat.completions.create(
            model=CRITIC_MODEL, temperature=0.0, max_tokens=1000, extra_body=CRITIC_EXTRA,
            messages=[{"role": "system", "content": VALIDATE_SYS},
                      {"role": "user", "content": user}])
        try:
            d = reply_json(resp.choices[0].message, lambda v: isinstance(v, dict) and ("pass" in v or "critical" in v))
            crit = d.get("critical", [])
            if CRITIC_MODE == "completeness":
                # Keep only hard blockers of the completeness kind (missing file / unavailable capability);
                # leakage and difficulty are decided by the measured difficulty gate
                keep = [c for c in crit if any(k in str(c).upper() for k in
                        ("COMPLETE", "MISSING", "NOT PRESENT", "API KEY", "MCP", "BROWSER",
                         "JAVA", "PHP", "DATABASE", "PRE-EXISTING", "INTERACTIVE"))]
                return (len(keep) == 0), keep, d.get("minor", []) + [c for c in crit if c not in keep]
            return bool(d.get("pass")), crit, d.get("minor", [])
        except Exception:
            continue
    return True, [], []  # no parseable critic reply after three attempts: let the task through


async def dryrun_difficulty(cli, spec) -> float:
    """Difficulty gate: the target model attempts the task unaided (no skill) -> a judge scores it against the TC
    rubric -> returns the pass rate. A high pass rate = the task does not carry the skill's crux = no information
    for measuring internalization -> should be rejected."""
    tc = spec.get("task_completion_rubric") or []
    if not tc:
        return 1.0
    inputs = json.dumps(spec.get("inputs", []), ensure_ascii=False)[:3000]
    user = f"# Task\n{spec.get('task_description','')[:4000]}\n\n# Available input files\n{inputs}"
    sol = ""
    for _ in range(2):
        try:
            resp = await cli.chat.completions.create(
                model=TARGET_MODEL, temperature=0.0, max_tokens=3000, extra_body=CRITIC_EXTRA,
                messages=[{"role": "system", "content": BARE_SOLVE_SYS},
                          {"role": "user", "content": user}])
            sol = msg_text(resp.choices[0].message)
            if sol.strip():
                break
        except Exception:
            continue
    if not sol.strip():
        return 1.0                                    # no unaided attempt obtained -> conservatively judge too easy (reject)
    rl = "\n".join(f"- {c.get('name','?')}: {c.get('description','')[:180]}" for c in tc)
    juser = f"# Rubric\n{rl[:3000]}\n\n# Solution (no skill)\n{sol[:6000]}"
    for _ in range(2):
        try:
            resp = await cli.chat.completions.create(
                model=TARGET_MODEL, temperature=0.0, max_tokens=1200, extra_body=CRITIC_EXTRA,
                messages=[{"role": "system", "content": DIFF_JUDGE_SYS},
                          {"role": "user", "content": juser}])
            d = reply_json(resp.choices[0].message, is_dict_with("passed"))
            names = {c.get("name") for c in tc}
            passed = [x for x in d.get("passed", []) if x in names]
            return len(passed) / len(tc)
        except Exception:
            continue
    return 1.0


def write_task(out_dir: Path, task_name, row, skill_unique, spec, idx) -> dict:
    td = out_dir / "tasks" / task_name
    td.mkdir(parents=True, exist_ok=True)
    (td / "task.md").write_text(spec["task_description"], encoding="utf-8")

    inputs = spec.get("inputs") or []
    if inputs:
        (td / "inputs").mkdir(parents=True, exist_ok=True)
        for f in inputs:
            fname = Path(f["path"]).name          # file name only: sub-directories under inputs/ are flattened
            (td / "inputs" / fname).write_text(f.get("content", ""), encoding="utf-8")

    package_skill(row, out_dir / "skills" / skill_unique)

    rub = td / "rubrics"
    rub.mkdir(parents=True, exist_ok=True)
    tc = {"context": f"This rubric evaluates scenario {idx}",
          "type": "weighted_checklist",
          "checklist": spec.get("task_completion_rubric", [])}
    iff = {"context": f"This rubric evaluates scenario {idx}",
           "type": "weighted_checklist",
           "checklist": spec.get("instruction_following_rubric", [])}
    (rub / "task-completion.json").write_text(
        json.dumps(tc, indent=1, ensure_ascii=False), encoding="utf-8")
    (rub / "instruction-following.json").write_text(
        json.dumps(iff, indent=1, ensure_ascii=False), encoding="utf-8")

    return {"task_name": task_name, "n_tc": len(tc["checklist"]),
            "n_if": len(iff["checklist"]),
            "tc_sum": sum(int(i.get("max_score", 0)) for i in tc["checklist"]),
            "if_sum": sum(int(i.get("max_score", 0)) for i in iff["checklist"]),
            "n_inputs": len(inputs)}


# ── main flow ──────────────────────────────────────────────────────

def _require(**kv):
    missing = [k for k, v in kv.items() if not v]
    if missing:
        sys.exit("missing required environment variable(s): " + ", ".join(missing))


async def amain():
    ap = argparse.ArgumentParser(description="Synthesize evaluation tasks + hidden rubrics from skills.")
    ap.add_argument("--skills-dir", required=True, help="directory with one sub-directory per skill (SKILL.md + bundled files)")
    ap.add_argument("--skills-csv", help="optional CSV listing the skills to use (columns: skill or path; optional name, source, description)")
    ap.add_argument("--id-from-source", action="store_true",
                    help="build the skill identifier from source (owner/repo) + name instead of using the directory name")
    ap.add_argument("--categories", help="optional JSONL {id, type, sub} with skill categories (recorded in synth_results.jsonl)")
    ap.add_argument("--limit", type=int, default=0, help="process at most N skills (the first N by id)")
    ap.add_argument("--sample", type=int, default=0, help="random sample of N skills")
    ap.add_argument("--seed", type=int, default=SAMPLE_SEED, help="random seed for --sample (default 42)")
    ap.add_argument("--exclude-from", help="exclude skill ids already present in this jsonl (avoid repeats)")
    ap.add_argument("--tasks-per-skill", type=int, default=1)
    ap.add_argument("--out", default="output")
    ap.add_argument("--no-validate", action="store_true")
    args = ap.parse_args()

    _require(GEN_BASE_URL=GEN_BASE_URL, GEN_API_KEY=GEN_API_KEY)
    if not args.no_validate:
        _require(CRITIC_BASE_URL=CRITIC_BASE_URL, CRITIC_API_KEY=CRITIC_API_KEY)
    if not NO_DIFF_GATE:
        _require(TARGET_BASE_URL=TARGET_BASE_URL, TARGET_API_KEY=TARGET_API_KEY)

    exclude_ids = set()
    if args.exclude_from:
        for line in open(args.exclude_from, encoding="utf-8"):
            try:
                d = json.loads(line)
                if d.get("id") is not None:
                    exclude_ids.add(d["id"])
            except Exception:
                pass
    type_by_id, sub_by_id = load_categories(args.categories)
    skills = load_skills(Path(args.skills_dir), args.skills_csv, args.id_from_source)
    rows = iter_skills(skills, args.limit, args.sample, exclude_ids, args.seed, type_by_id, sub_by_id)
    if not rows:
        sys.exit("no skill to synthesize from")
    print(f"[synthesize] sampled {len(rows)} skills, generator={GEN_MODEL}, critic={CRITIC_MODEL}, "
          f"target={TARGET_MODEL}, conc={CONC}", flush=True)

    out_dir = Path(args.out).resolve()
    (out_dir / "tasks").mkdir(parents=True, exist_ok=True)
    meta_path = out_dir / "metadata.csv"
    res_path = out_dir / "synth_results.jsonl"
    if not meta_path.exists():
        meta_path.write_text("task-name,task_type,task_group,task_idx_in_group,"
                             "task_description,skills-used,skills_github_sources\n", encoding="utf-8")

    cli = (AsyncOpenAI(base_url=CRITIC_BASE_URL, api_key=CRITIC_API_KEY)          # critic
           if CRITIC_BASE_URL and CRITIC_API_KEY else None)
    tcli = (AsyncOpenAI(base_url=TARGET_BASE_URL, api_key=TARGET_API_KEY)        # difficulty gate (unaided target model + judge)
            if TARGET_BASE_URL and TARGET_API_KEY else None)
    gcli = AsyncOpenAI(base_url=GEN_BASE_URL, api_key=GEN_API_KEY)                # generator
    sem = asyncio.Semaphore(CONC)
    lock = asyncio.Lock()
    t0 = time.time()
    cnt = [0]

    def csv_line(row_) -> str:
        buf = io.StringIO()
        csv.writer(buf).writerow(row_)
        return buf.getvalue()

    def append(path, text):
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)

    async def one(row):
        sid, name, src = row["id"], row["name"], row["source"]
        skill_unique = row["skill_unique"]
        url = source_url(src)
        async with sem:
            for idx in range(args.tasks_per_skill):
                task_name = f"{TASK_PREFIX}{skill_unique}_{idx}"
                td = out_dir / "tasks" / task_name
                if (td / "rubrics" / "instruction-following.json").exists():
                    continue
                try:
                    spec = await generate(gcli, row)
                    crit, minor = [], []
                    if not args.no_validate:
                        ok, crit, minor = await validate(cli, row, spec)
                        for _ in range(GEN_TRIES - 1):
                            if ok:
                                break
                            spec = await generate(gcli, row, feedback=crit)
                            ok, crit, minor = await validate(cli, row, spec)
                        if not ok:
                            async with lock:
                                append(res_path, json.dumps(
                                    {"id": sid, "name": name, "source": src,
                                     "type": row["type"], "sub": row["sub"],
                                     "status": "rejected", "critical": crit},
                                    ensure_ascii=False) + "\n")
                            print(f"REJECT {name}: {crit[:2]}", flush=True)
                            return
                    # Difficulty gate: unaided pass rate of the target model too high = task does not carry the
                    # crux -> reject (no information for measuring internalization)
                    bare = None
                    if not NO_DIFF_GATE:
                        bare = await dryrun_difficulty(tcli, spec)
                        tries = 0
                        while bare > DIFF_MAX and tries < GEN_TRIES - 1:
                            tries += 1
                            spec = await generate(gcli, row, feedback=[
                                f"The task was TOO EASY: a model without the skill passed "
                                f"{bare:.0%} of the task-completion rubric. Make the rubric test the "
                                f"skill's specific crux knowledge that a general model would not have."])
                            if not args.no_validate:
                                ok2, crit2, _ = await validate(cli, row, spec)
                                if not ok2:
                                    continue
                            bare = await dryrun_difficulty(tcli, spec)
                        if bare > DIFF_MAX:
                            async with lock:
                                append(res_path, json.dumps(
                                    {"id": sid, "name": name, "source": src,
                                     "type": row["type"], "sub": row["sub"],
                                     "status": "too_easy", "bare_pass": round(bare, 3)},
                                    ensure_ascii=False) + "\n")
                            print(f"TOO_EASY {name}: bare_pass={bare:.2f}", flush=True)
                            return
                    info = write_task(out_dir, task_name, row, skill_unique, spec, idx)
                    meta_row = [task_name, "single_skill",
                                task_name.rsplit("_", 1)[0], str(idx),
                                spec["task_description"],
                                json.dumps([skill_unique]), json.dumps([url])]
                    async with lock:
                        append(res_path, json.dumps(
                            {"id": sid, "name": name, "source": src, "bare_pass": bare,
                             "type": row["type"], "sub": row["sub"],
                             "status": "ok", **info}, ensure_ascii=False) + "\n")
                        append(meta_path, csv_line(meta_row))
                    cnt[0] += 1
                    print(f"DONE {task_name}  tc={info['n_tc']}(sum{info['tc_sum']}) "
                          f"if={info['n_if']}(sum{info['if_sum']}) in={info['n_inputs']}"
                          + (f"  minor={minor[:1]}" if minor else ""), flush=True)
                except Exception as e:
                    async with lock:
                        append(res_path, json.dumps(
                            {"id": sid, "name": name, "source": src,
                             "type": row["type"], "sub": row["sub"], "status": "error",
                             "err": str(e)[:500]}, ensure_ascii=False) + "\n")
                    print(f"ERR {name}: {e}", flush=True)
                    return

    await asyncio.gather(*[one(r) for r in rows])
    print(f"[synthesize] DONE {cnt[0]} tasks, {time.time()-t0:.0f}s -> {out_dir}",
          flush=True)


if __name__ == "__main__":
    asyncio.run(amain())
