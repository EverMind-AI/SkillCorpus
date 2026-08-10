#!/usr/bin/env python3
"""End-to-end demo: task description -> SkillHub -> skills -> agent prompt.

This is the whole loop SkillCorpus is for. Two modes:

    # 1. just retrieve — no LLM needed, no API key
    python examples/skillhub_demo.py "extract tables from a scanned PDF invoice"

    # 2. retrieve AND run the task, with the skills injected into the prompt
    export OPENAI_API_KEY=...  OPENAI_BASE_URL=https://...
    python examples/skillhub_demo.py --ask "extract tables from a scanned PDF invoice"

Configuration (env vars):
    SKILLHUB_URL     SkillHub base URL          (default: the public endpoint)
    SKILLHUB_TOKEN   bearer token, if required  (default: none)
    OPENAI_BASE_URL  OpenAI-compatible endpoint, only needed for --ask
    OPENAI_API_KEY   ditto
    OPENAI_MODEL     model name for --ask       (default: gpt-4o-mini)

Stdlib only — no dependency on the skillcorpus package itself.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
import urllib.error
import urllib.request

# TODO(@team): point at the real deployment and confirm the request/response
# shape below. Everything provider-specific is isolated in search_skills().
DEFAULT_SKILLHUB_URL = os.environ.get("SKILLHUB_URL", "https://<SKILLHUB_URL>")
SEARCH_PATH = "/v1/skills/search"


def _post(url: str, payload: dict, token: str | None, timeout: int = 30) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def search_skills(query: str, top_k: int = 3) -> list[dict]:
    """Ask SkillHub which skills fit this task.

    Returns a list of dicts with at least ``name``, ``description``, ``body``,
    ``license``, ``source_url``.
    """
    base = DEFAULT_SKILLHUB_URL.rstrip("/")
    token = os.environ.get("SKILLHUB_TOKEN")
    try:
        data = _post(f"{base}{SEARCH_PATH}", {"query": query, "top_k": top_k}, token)
    except urllib.error.HTTPError as e:
        sys.exit(f"SkillHub returned HTTP {e.code}: {e.read()[:200].decode(errors='replace')}")
    except (urllib.error.URLError, OSError) as e:
        sys.exit(
            f"Could not reach SkillHub at {base}: {e}\n"
            f"Set SKILLHUB_URL to your deployment, e.g.\n"
            f"    export SKILLHUB_URL=https://skillhub.example.com"
        )
    return data.get("skills", [])


def build_prompt(task: str, skills: list[dict]) -> str:
    """Inject the retrieved skill bodies ahead of the task. This is the only
    integration step — any harness that can prepend text to a system prompt
    can do the same thing."""
    blocks = "\n\n".join(
        f"<skill name=\"{s['name']}\">\n{s['body']}\n</skill>" for s in skills
    )
    return (
        "You have been given the following skills. Follow them when they apply.\n\n"
        f"{blocks}\n\n"
        f"Task: {task}"
    )


def ask_llm(prompt: str) -> str:
    base = os.environ.get("OPENAI_BASE_URL")
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        sys.exit("--ask needs OPENAI_API_KEY (and OPENAI_BASE_URL for a non-OpenAI endpoint)")
    try:
        from openai import OpenAI
    except ImportError:
        sys.exit("--ask needs the openai package:  pip install openai")
    client = OpenAI(base_url=base, api_key=key)
    resp = client.chat.completions.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content or ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("task", help="what you want the agent to do, in plain language")
    ap.add_argument("--top-k", type=int, default=3, help="how many skills to retrieve")
    ap.add_argument("--ask", action="store_true",
                    help="also run the task through an LLM with the skills injected")
    ap.add_argument("--show-body", action="store_true",
                    help="print the full SKILL.md body of each hit")
    args = ap.parse_args()

    print(f"task: {args.task}\n")
    skills = search_skills(args.task, args.top_k)
    if not skills:
        print("SkillHub returned no skills for this task.")
        return 1

    print(f"SkillHub returned {len(skills)} skill(s):\n")
    for i, s in enumerate(skills, 1):
        score = s.get("score")
        print(f"  {i}. {s['name']}"
              + (f"   (score {score:.3f})" if isinstance(score, (int, float)) else ""))
        print(textwrap.fill(s.get("description", ""), 78,
                            initial_indent="     ", subsequent_indent="     "))
        print(f"     {s.get('license', '?')} · {s.get('source_url', '')}")
        if args.show_body:
            print(textwrap.indent(s.get("body", ""), "     | "))
        print()

    prompt = build_prompt(args.task, skills)
    print(f"→ built a prompt of {len(prompt)} chars with the skills injected")

    if args.ask:
        print("\n--- agent output ---")
        print(ask_llm(prompt))
    else:
        print("  (re-run with --ask to actually execute the task)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
