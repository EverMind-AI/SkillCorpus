#!/usr/bin/env python3
"""End-to-end demo: task description -> SkillHub -> skill -> agent prompt.

SkillHub serves the corpus over three tiers, cheapest first:

    1. GET /openapi/v1/skills?q=...            metadata only, no body   (cheap)
    2. GET /openapi/v1/skills/{ref}            adds skill_md + subscores (cheap)
    3. GET /openapi/v1/skills/{ref}/download   zip with scripts/assets  (only if needed)

Most skills are pure instructions — tier 2 is enough, and its `skill_md` is what
you inject into the agent prompt. Only download when a skill ships scripts you
actually intend to execute.

Usage:

    # search + read the bodies (no install, no API key)
    python examples/skillhub_demo.py "extract tables from a scanned PDF invoice"

    # also run the task with the skill bodies injected into the prompt
    export OPENAI_API_KEY=...
    python examples/skillhub_demo.py --ask "extract tables from a scanned PDF invoice"

    # fetch the bundled scripts of the top hit into ./skills/
    python examples/skillhub_demo.py --install ./skills "convert a PDF to images"

Environment:
    SKILLHUB_URL     base URL      (default: https://skillhub.evermind.ai)
    SKILLHUB_TOKEN   bearer token  (optional; public skills need none)
    OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL   only for --ask

Stdlib only — no dependency on the skillcorpus package.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
import zipfile

BASE = os.environ.get("SKILLHUB_URL", "https://skillhub.evermind.ai").rstrip("/")
API = "/openapi/v1"

# status codes carried inside the JSON envelope (HTTP status mirrors them)
_STATUS_TEXT = {
    60001: "skill not found",
    60002: "invalid parameter",
    60003: "download failed",
    60005: "rate limited — search/detail allow 120/min, download 30/min per IP",
    20001: "internal error",
}


def _get(path: str, params: dict | None = None, raw: bool = False):
    """GET <BASE><path>. Unwraps the JSON envelope unless ``raw`` (zip bytes)."""
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    headers = {"Accept": "*/*"}
    token = os.environ.get("SKILLHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                    timeout=30) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as e:
        body = e.read()
        try:                                    # errors still come back enveloped
            env = json.loads(body)
            code = env.get("status")
            sys.exit(f"SkillHub error {code}: {_STATUS_TEXT.get(code, env.get('error'))}")
        except json.JSONDecodeError:
            sys.exit(f"SkillHub returned HTTP {e.code}: {body[:200]!r}")
    except (urllib.error.URLError, OSError) as e:
        sys.exit(f"Could not reach SkillHub at {BASE}: {e}\n"
                 f"Override with:  export SKILLHUB_URL=https://your-host")

    if raw:
        return payload
    env = json.loads(payload)
    if env.get("status") != 0:                  # status == 0 means success
        code = env.get("status")
        sys.exit(f"SkillHub error {code}: {_STATUS_TEXT.get(code, env.get('error'))}")
    return env["result"]


def search(query: str, *, limit: int = 3, category: str | None = None,
           min_score: float | None = None, tags: str | None = None) -> list[dict]:
    """Tier 1 — metadata only, no body. ``/skills/search`` takes the filters;
    plain ``/skills?q=`` is the unfiltered, unpaginated shortcut."""
    result = _get(f"{API}/skills/search", {
        "q": query, "limit": limit, "category": category,
        "min_score": min_score, "tags": tags,
    })
    return result.get("items", [])


def get_skill(ref: str) -> dict:
    """Tier 2 — full record including ``skill_md`` (the body you inject),
    ``files``, ``subscores`` and ``safety_flags``. ``ref`` is the UUID ``id``
    or the raw ``skill_id``."""
    return _get(f"{API}/skills/{urllib.parse.quote(ref, safe='')}")


def download(ref: str, out_dir: str, source: str = "cli") -> list[str]:
    """Tier 3 — zip with scripts/assets. Only for skills you will execute.
    ``source`` must be one of raven | everme | cli | web (it records an
    install event); anything else is rejected with 60002."""
    blob = _get(f"{API}/skills/{urllib.parse.quote(ref, safe='')}/download",
                {"source": source}, raw=True)
    tmp = os.path.join(out_dir, ".skillhub-download.zip")
    os.makedirs(out_dir, exist_ok=True)
    with open(tmp, "wb") as fh:
        fh.write(blob)
    written = []
    with zipfile.ZipFile(tmp) as zf:
        for member in zf.namelist():
            # The archive wraps everything in a <skill-name>/ dir, but never
            # trust the paths inside it — reject absolute / traversing entries.
            dest = os.path.normpath(os.path.join(out_dir, member))
            if not dest.startswith(os.path.abspath(out_dir) + os.sep) and \
               not dest.startswith(os.path.normpath(out_dir) + os.sep):
                print(f"  !! skipped unsafe zip entry: {member}", file=sys.stderr)
                continue
            zf.extract(member, out_dir)
            written.append(member)
    os.remove(tmp)
    return written


def build_prompt(task: str, skills: list[dict]) -> str:
    """The whole integration step: prepend the skill bodies to the task.
    Any harness that can edit a system prompt can do exactly this."""
    blocks = "\n\n".join(
        f'<skill name="{s["name"]}">\n{s["skill_md"]}\n</skill>' for s in skills
    )
    return ("You have been given the following skills. Follow them when they apply.\n\n"
            f"{blocks}\n\nTask: {task}")


def ask_llm(prompt: str) -> str:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        sys.exit("--ask needs OPENAI_API_KEY (and OPENAI_BASE_URL for a non-OpenAI endpoint)")
    try:
        from openai import OpenAI
    except ImportError:
        sys.exit("--ask needs the openai package:  pip install openai")
    client = OpenAI(base_url=os.environ.get("OPENAI_BASE_URL"), api_key=key)
    resp = client.chat.completions.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content or ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("task", help="what you want the agent to do, in plain language")
    ap.add_argument("--top-k", type=int, default=3, help="how many skills to retrieve (1-50)")
    ap.add_argument("--category", help="filter by 16-class category, e.g. DOC-PROC")
    ap.add_argument("--min-score", type=float, help="minimum quality_score, 0-1")
    ap.add_argument("--ask", action="store_true",
                    help="run the task through an LLM with the skill bodies injected")
    ap.add_argument("--install", metavar="DIR",
                    help="download the top hit's bundle (scripts/assets) into DIR")
    args = ap.parse_args()

    print(f"task: {args.task}\n")
    hits = search(args.task, limit=args.top_k,
                  category=args.category, min_score=args.min_score)
    if not hits:
        print("SkillHub returned no skills for this task.")
        return 1

    print(f"[1/2] search  → {len(hits)} hit(s), metadata only")
    for i, h in enumerate(hits, 1):
        print(f"  {i}. {h['name']}   q={h['quality_score']}  {h['category']}"
              f"  {h.get('license') or 'license: ?'}")
        print(textwrap.fill(h["description"], 78,
                            initial_indent="     ", subsequent_indent="     "))

    print(f"\n[2/2] detail  → fetching skill_md for {len(hits)} skill(s)")
    full = [get_skill(h["id"]) for h in hits]
    for s in full:
        sub = s.get("subscores") or {}
        print(f"  {s['name']}: {len(s['skill_md'])} chars"
              f"  u={sub.get('utility')} r={sub.get('robustness')} s={sub.get('safety')}"
              f"  files={len(s.get('files') or [])}"
              + (f"  flags={s['safety_flags']}" if s.get("safety_flags") else ""))

    if args.install:
        top = full[0]
        print(f"\n[3] download → {top['name']} bundle into {args.install}")
        for m in download(top["id"], args.install):
            print(f"     {m}")

    prompt = build_prompt(args.task, full)
    print(f"\n→ built a prompt of {len(prompt)} chars with the skill bodies injected")
    if args.ask:
        print("\n--- agent output ---")
        print(ask_llm(prompt))
    else:
        print("  (re-run with --ask to actually execute the task)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
