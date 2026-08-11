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

    # run the task with the skill bodies injected — any OpenAI-compatible endpoint
    export OPENAI_API_KEY=...                              # your provider's key
    python examples/skillhub_demo.py --ask "extract tables from a scanned PDF invoice"

    # e.g. OpenRouter (or a local vLLM / Together / …): set the base URL + model
    export OPENAI_BASE_URL=https://openrouter.ai/api/v1
    python examples/skillhub_demo.py --ask --model openai/gpt-4o-mini "…"

    # fetch the bundled scripts of the top hit into ./skills/
    python examples/skillhub_demo.py --install ./skills "convert a PDF to images"

Environment:
    SKILLHUB_URL     base URL      (default: https://skillhub.evermind.ai)
    SKILLHUB_TOKEN   bearer token  (optional; public skills need none)
    OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL   only for --ask; any
        OpenAI-compatible provider works (OpenAI, OpenRouter, a local vLLM, …)

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


def search(query: str, *, limit: int = 3) -> list[dict]:
    """Tier 1 — metadata only, no body. The query is embedded, matched by
    vector ANN, then reranked by the cross-encoder; the endpoint decides how
    many hits come back, so ``limit`` trims them client-side."""
    result = _get(f"{API}/skills", {"q": query})
    return result.get("items", [])[:limit]


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


def ask_llm(prompt: str, model: str | None = None) -> str:
    """Run the prompt through any OpenAI-compatible chat endpoint — OpenAI,
    OpenRouter, Together, a local vLLM, etc. Pick the provider with
    OPENAI_BASE_URL, the model with --model / OPENAI_MODEL, and authenticate
    with OPENAI_API_KEY."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        sys.exit("--ask needs OPENAI_API_KEY (+ OPENAI_BASE_URL for a non-OpenAI "
                 "provider, e.g. OpenRouter: https://openrouter.ai/api/v1)")
    try:
        from openai import OpenAI
    except ImportError:
        sys.exit("--ask needs the openai package:  pip install openai")
    base_url = os.environ.get("OPENAI_BASE_URL")
    model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    print(f"  (llm: {model} @ {base_url or 'api.openai.com'})", file=sys.stderr)
    client = OpenAI(base_url=base_url, api_key=key)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content or ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("task", help="what you want the agent to do, in plain language")
    ap.add_argument("--top-k", type=int, default=3, help="how many of the hits to keep")
    ap.add_argument("--ask", action="store_true",
                    help="run the task through an LLM with the skill bodies injected")
    ap.add_argument("--model",
                    help="model id for --ask (default: $OPENAI_MODEL or gpt-4o-mini); "
                         "for OpenRouter set OPENAI_BASE_URL and use e.g. "
                         "openai/gpt-4o-mini or anthropic/claude-3.5-sonnet")
    ap.add_argument("--install", metavar="DIR",
                    help="download the top hit's bundle (scripts/assets) into DIR")
    args = ap.parse_args()

    print(f"task: {args.task}\n")
    hits = search(args.task, limit=args.top_k)
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
        print(ask_llm(prompt, args.model))
    else:
        print("  (re-run with --ask to actually execute the task)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
