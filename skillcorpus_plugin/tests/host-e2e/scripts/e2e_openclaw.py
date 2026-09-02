"""Cases in both modes, through a real OpenClaw agent turn — either generation.

`openclaw agent --local` runs the embedded agent against a profile's own
config, which is where the plugin is registered — so this drives the host's
plugin loader, its tool surface and its injection path, not a hand-built
context.

Two hosts, two packages, one script, because the cases and the way they are
judged have to be identical for the results to mean anything side by side:

    --generation 1   OpenClaw <=2026.7.x, `plugin-openclaw`
                     auto: a `before_prompt_build` hook
    --generation 2   OpenClaw 2.0 (2026.8.x), `plugin-openclaw2`
                     auto: the context-engine slot

    auto       the injection path carries the skill; `skill_search` absent
    on_demand  `skill_search` is registered and the model has to call it

What differs between the generations is three lines of config, all in
`write_profile`, and they are marked. 2.0 also renamed `agents.list` to a
keyed `agents.entries`, which the older shape does not survive.

Usage:

    export SKILLSEARCH_E2E_BASE_URL=... SKILLSEARCH_E2E_MODEL=...
    python e2e_openclaw.py --generation 2 \
        --openclaw /path/to/node_modules/.bin/openclaw

## Where the evidence comes from

Not the reply. This host's agent has `read`, `dir_list` and `exec`, and the
skill body is a file on disk — so a reply full of fixture facts says nothing
about how they arrived. The turn's transcript, which the host writes to
`agents/<id>/agent/openclaw-agent.sqlite`, has the actual tool calls with their
arguments and results, and that is what this reads.

## One session per run, always

The first version of this script did not set `--session-id`, so every case
landed in one conversation. Case P1 called `skill_search`, whose result names
the skill's directory; case P2 then went straight to `read` on that path,
because the model already knew it. P2 looked like a tool-description failure
and was nothing of the kind. A fresh session per run is not hygiene here, it
is the difference between a real result and a wrong one.

## Gates

Retrieval in `auto` is inert, without an error, unless three things are set:
`plugins.entries.skillsearch.hooks.allowConversationAccess`,
`...hooks.allowPromptInjection` and `plugins.slots.contextEngine`. Note where
the first two live — they are grants on the plugin entry, not a top-level
`hooks` block, and writing them at the top level makes the host reject the
whole config. A missing grant, by contrast, looks exactly like a plugin that
found nothing, so this writes all three and names them here.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _e2e

RUN_TIMEOUT_S = 600.0


def write_profile(profile_dir: Path, generation: int, mode: str, skills: Path,
                  workspace: Path, model: dict, *, broken_source: bool = False) -> None:
    """The profile's `openclaw.json`, carrying the plugin and its gates."""
    # `parents[3]`, not `[2]`: the chain starts at this file, so it runs
    # scripts -> host-e2e -> tests -> skillcorpus_plugin.
    root = Path(__file__).resolve().parents[3]
    plugin = root / ("plugin-openclaw2" if generation == 2 else "plugin-openclaw")
    config: dict = {
        "plugins": {
            "load": {"paths": [str(plugin)]},
            "entries": {
                "skillsearch": {
                    "enabled": True,
                    "config": {
                        "mode": mode,
                        "skillsDirs": [str(skills)],
                        # The rewriter and gate are two more model calls on the
                        # turn's hot path and neither is what these cases
                        # measure; off keeps a slow deployment from reading as
                        # a retrieval failure.
                        "rewrite": False,
                        "gate": False,
                        "topK": 1,
                        # Every remote catalogue off — see `e2e_hermes.py`.
                        # Under `--broken-source` the first one points at a
                        # closed port instead, which is case P5.
                        **_e2e.camel(_e2e.source_endpoints(broken=broken_source)),
                    },
                }
            },
        },
        "models": {
            "mode": "merge",
            "providers": {
                "local": {
                    "baseUrl": model["base_url"],
                    "apiKey": model["api_key"],
                    "api": "openai-completions",
                    "models": [{"id": model["model"], "name": model["model"]}],
                }
            },
        },
        "agents": {
            "defaults": {"model": f"local/{model['model']}"},
            # 2.0 moved this off the `list` array 1.x takes. The old shape is
            # warned about and then ignored there, which reads as the
            # workspace setting simply not applying.
            **({"entries": {"main": {"workspace": str(workspace)}}} if generation == 2
               else {"list": [{"id": "main", "workspace": str(workspace)}]}),
        },
        "gateway": {"mode": "local"},
    }
    if mode == "auto":
        # Per plugin entry, not top level, on both generations. A top-level
        # `hooks` block is rejected outright — `Unrecognized keys` — so
        # getting this wrong costs the whole run rather than just the grant.
        config["plugins"]["entries"]["skillsearch"]["hooks"] = {
            "allowConversationAccess": True,
            "allowPromptInjection": True,
        }
        if generation == 2:
            # 2.0 only. 1.x injects from a `before_prompt_build` hook and has
            # no context-engine slot to claim.
            config["plugins"]["slots"] = {"contextEngine": "skillsearch"}
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "openclaw.json").write_text(json.dumps(config, indent=1), encoding="utf-8")


def transcript(profile_dir: Path, session_id: str) -> list[dict]:
    """Every transcript message for one session, oldest first.

    The two generations store this differently and neither is documented as an
    interface, so both are read and whichever exists wins: 1.x appends JSONL
    per session under `agents/<id>/sessions/`, 2.0 keeps `transcript_events`
    in a SQLite database. Opened read-only either way — this is the host's
    state, not the harness's.
    """
    jsonl = profile_dir / "agents" / "main" / "sessions" / f"{session_id}.jsonl"
    if jsonl.exists():
        events = []
        for line in jsonl.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    db = profile_dir / "agents" / "main" / "agent" / "openclaw-agent.sqlite"
    if not db.exists():
        return []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "select event_json from transcript_events where session_id = ? order by seq",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    events = []
    for (blob,) in rows:
        try:
            events.append(json.loads(blob))
        except json.JSONDecodeError:
            continue
    return events


def read_turn(events: list[dict]) -> dict:
    """What the model called, what came back, and what it knew unaided.

    `first_move` is the one that took work to get right. `auto` has no tool to
    watch, and the host does not persist the assembled context — so the
    question "did injection deliver the skill" has to be answered from what
    the model did *before* any tool had returned anything. Its first assistant
    turn is that: text plus the arguments of the tool call it opens with,
    concatenated. If the corpus path or the fixture facts are in there, they
    came from the injected block, because nothing else in the turn had them
    yet and the corpus sits under a randomly named temporary directory.
    """
    calls: list[dict] = []
    results: list[str] = []
    assistant_text: list[str] = []
    first_move = ""
    for event in events:
        message = event.get("message") or {}
        role = message.get("role")
        move: list[str] = []
        for part in message.get("content") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "toolCall":
                calls.append({"name": part.get("name"), "arguments": part.get("arguments")})
                move.append(json.dumps(part.get("arguments"), ensure_ascii=False))
            elif part.get("type") == "text":
                if role == "assistant":
                    assistant_text.append(str(part.get("text") or ""))
                    move.append(str(part.get("text") or ""))
                elif role == "toolResult":
                    results.append(f"{message.get('toolName')}\n{part.get('text') or ''}")
        if role == "assistant" and not first_move and not results:
            first_move = "\n".join(move)
    return {
        "tool_calls": calls,
        "tool_results": results,
        "first_move": first_move,
        "reply": "\n".join(assistant_text).strip(),
    }


def run(openclaw: Path, profile: str, prompt: str, session_id: str) -> dict:
    started = time.time()
    proc = subprocess.run(
        [str(openclaw), "--profile", profile, "agent", "--local",
         "--thinking", "off", "--session-id", session_id, "--json", "-m", prompt],
        capture_output=True, text=True, timeout=RUN_TIMEOUT_S, check=False,
    )
    return {
        "returncode": proc.returncode,
        "elapsed_s": round(time.time() - started, 1),
        "stderr": proc.stderr[-2000:],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("modes", nargs="*", default=["auto", "on_demand"])
    ap.add_argument("--openclaw", type=Path,
                    default=os.environ.get("SKILLSEARCH_E2E_OPENCLAW"),
                    help="the openclaw executable (node_modules/.bin/openclaw)")
    ap.add_argument("--generation", type=int, default=2, choices=(1, 2),
                    help="1 for OpenClaw <=2026.7.x (plugin-openclaw), "
                         "2 for OpenClaw 2.0 (plugin-openclaw2)")
    ap.add_argument("--profile", default="skillsearch-e2e",
                    help="OpenClaw profile name; state is isolated under "
                         "~/.openclaw-<name> and ~/.openclaw is never touched")
    ap.add_argument("--case", default="p1", choices=sorted(_e2e.CASES))
    ap.add_argument("--broken-source", action="store_true",
                    help="case P5: point one remote catalogue at a closed port "
                         "and check the local corpus, the turn and the log all "
                         "survive it")
    ap.add_argument("--restart", action="store_true",
                    help="case P6: run the same turn twice against the same "
                         "profile, with the config untouched in between, and "
                         "check the second run still loads the plugin and "
                         "still retrieves")
    ap.add_argument("--mode-typo", default=None, metavar="VALUE",
                    help="case P4: write this as the `mode` instead of the real "
                         "one, and check the host logs the narrowing rather "
                         "than silently running the opposite mode")
    ap.add_argument("--dump", type=Path, default=None)
    args = ap.parse_args()
    if not args.openclaw:
        ap.error("--openclaw or SKILLSEARCH_E2E_OPENCLAW is required")

    model = _e2e.model_config()
    profile_dir = Path.home() / f".openclaw-{args.profile}"
    prompt = _e2e.CASES[args.case]["prompt"]
    print(f"openclaw={args.openclaw} generation={args.generation} "
          f"profile={args.profile} model={model['model']}")

    results, failures = {}, []
    for mode in args.modes:
        workspace = Path(tempfile.mkdtemp(prefix=f"oc{args.generation}-ws-{mode}-"))
        # Outside the workspace on purpose, unlike the other hosts: this
        # agent's `read` and `exec` reach anywhere, and a corpus it can browse
        # makes "the body arrived" unanswerable. The tool result names the
        # directory, so the agent still finds it the moment retrieval works.
        skills = _e2e.corpus()
        write_profile(profile_dir, args.generation, args.mode_typo or mode, skills,
                      workspace, model, broken_source=args.broken_source)
        session_id = str(uuid.uuid4())
        try:
            out = run(Path(args.openclaw), args.profile, prompt, session_id)
            out.update(read_turn(transcript(profile_dir, session_id)))
            if args.restart:
                # A second process against the same profile directory, with
                # nothing rewritten in between. That is what "restart the
                # host" means here: the config on disk and the plugin's own
                # state are all that carry over, and a fresh session id keeps
                # the second turn from inheriting the first one's answers.
                second_id = str(uuid.uuid4())
                second = run(Path(args.openclaw), args.profile, prompt, second_id)
                second.update(read_turn(transcript(profile_dir, second_id)))
                out["after_restart"] = {
                    "session_id": second_id,
                    "returncode": second["returncode"],
                    "tool_calls": [c["name"] for c in second["tool_calls"]],
                    "reply": second["reply"],
                    "elapsed_s": second["elapsed_s"],
                }
                # Judged the same way as the first run, so "it still works"
                # means the same thing both times rather than "it did not
                # crash".
                out["restart_ok"] = (
                    second["returncode"] == 0
                    and (("skill_search" in out["after_restart"]["tool_calls"])
                         if mode == "on_demand"
                         else _e2e.sentinel_in(second["reply"])
                         or str(skills) in second["first_move"])
                )
        finally:
            shutil.rmtree(workspace, ignore_errors=True)
            shutil.rmtree(skills.parent, ignore_errors=True)

        searches = [c for c in out["tool_calls"] if c["name"] == "skill_search"]
        retrieved = "\n".join(r for r in out["tool_results"] if r.startswith("skill_search"))
        # Injection delivered the skill if the model's opening move already
        # knew about it — either quoting the body or reaching for the corpus
        # path, which is randomly named and cannot be guessed. Reading the
        # file afterwards is what a served agent does with a skill it was
        # handed; it is not evidence against the injection.
        # Two grades of the same evidence, recorded separately because they
        # are not equally direct. "facts" means the opening move quotes the
        # body, so injection put it in front of the model. "path" means the
        # move reaches for the corpus directory — injection named the skill
        # and the model went to read it, which is what a served agent does,
        # but the body then arrived via `read` rather than in the context.
        if _e2e.sentinel_in(out["first_move"]):
            evidence = "facts"
        elif str(skills) in out["first_move"]:
            evidence = "path"
        else:
            evidence = "none"
        out["auto_evidence"] = evidence
        injected = evidence != "none"
        # For on-demand this is the tool result and nothing else. For auto it
        # is the opening move plus the reply, which together are the only
        # window onto a channel the host does not persist.
        delivered = retrieved if searches else ""
        if injected:
            delivered = f"{delivered}\n{out['first_move']}\n{out['reply']}"
        ok, facts = _e2e.verdict(
            case=args.case,
            mode=mode,
            tool_offered=bool(searches) or mode == "on_demand",
            auto_channel_filled=injected,
            tool_called=bool(searches),
            delivered=delivered,
            reply=out["reply"],
        )
        out["session_id"] = session_id
        out["corpus"] = str(skills)
        out["skill_search_queries"] = [c["arguments"] for c in searches]
        out["verdict"] = {"pass": ok, **facts}
        results[mode] = out
        print(_e2e.line(mode, ok, facts, out["elapsed_s"]))
        print(f"             tools={[c['name'] for c in out['tool_calls']]} "
              f"queries={out['skill_search_queries']} "
              f"auto_evidence={out['auto_evidence']}")
        print(f"             reply: {out['reply'][:220]!r}")
        if args.restart:
            after = out.get("after_restart", {})
            print(f"             after restart: rc={after.get('returncode')} "
                  f"tools={after.get('tool_calls')} "
                  f"{'OK' if out.get('restart_ok') else 'FAILED'} "
                  f"({after.get('elapsed_s')}s)")
            if not out.get("restart_ok"):
                ok = False
                failures.append(f"{mode}:restart")
        if not ok:
            failures.append(mode)

    if args.dump:
        args.dump.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print("all passed" if not failures else f"failed: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
