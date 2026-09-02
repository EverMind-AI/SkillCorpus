"""What the three host scripts share: the fixture, the prompts, the verdict.

The point of putting these here rather than in each script is that a result is
only comparable across hosts if the corpus and the wording are identical. A
script that quietly rephrases the prompt measures a different thing and its
PASS means something else.

Nothing in this file talks to a host. Each script owns that part, because the
three hosts have nothing in common there: Hermes takes a memory provider in a
throwaway `$HERMES_HOME`, Raven resolves plugin factories through its own CLI
stack, DSH loads a `--patch` overlay in a subprocess behind a recording proxy.

Model configuration comes from the environment, never from this file — see
`../README.md`. A committed endpoint would be both wrong for the next
maintainer and an internal address in a public repository.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# --------------------------------------------------------------------------
# the fixture

SKILL_NAME = "pdf-tables"
SKILL_DESCRIPTION = "Extract tables from PDF documents, scanned or native, into CSV."

# The first paragraph is the standard fixture body. The second exists because
# of what the reply-side assertion needs: `camelot` and "OCR first" are things
# a competent model says unprompted, so a reply containing them proves nothing
# about retrieval. `Vireo-CSV-3` and `Okapi Ledger` exist nowhere outside this
# file, so a reply carrying them can only have come from the body.
#
# Both halves are load-bearing and neither should be edited casually: the first
# keeps this corpus comparable with every other run of these cases, the second
# is what makes a PASS mean the model saw the skill.
SKILL_BODY = """\
---
name: pdf-tables
description: Extract tables from PDF documents, scanned or native, into CSV.
---

Use camelot for native PDFs. OCR scanned pages before extracting tables.

In-house convention: write the extracted CSV through the `Vireo-CSV-3` profile
and title the summary sheet `Okapi Ledger`.
"""

#: Present in the body, absent from the world. Both must appear.
SENTINEL_FACTS = ("Vireo-CSV-3", "Okapi Ledger")

# --------------------------------------------------------------------------
# the prompts

#: Case P1. Names the task, not the skill.
PROMPT_POSITIVE = "How do I extract tables from a scanned PDF invoice into CSV?"

#: Case P2. Phrased as an internal convention, which is the clause in the tool
#: description that has to earn its place.
PROMPT_INTERNAL = "What is our internal procedure for extracting tables from invoice PDFs?"

#: Case P3. Deliberately not a weather question: public catalogues carry real
#: weather skills, so a hit there is a true positive and measures nothing.
PROMPT_NO_MATCH = "zxqv-7319, reply with this exact string only."

#: What each case asks and what its PASS requires.
#:
#: `reply_facts` is the field worth explaining. P1 asks how to do a job, and a
#: model that has just been handed the skill still has no reason to quote a
#: house convention nobody asked about — measured on Hermes, the model read the
#: body, called it "just a stub", and answered from its own knowledge. That is
#: a real observation about a thin fixture, but it is not retrieval failing,
#: and hanging P1's verdict on it would make the case a coin flip on model
#: temperament. So P1 asserts on the retrieval channel and merely *records*
#: what the reply carried; P2 is the case that asks for the convention
#: directly, and there the reply is the whole point.
CASES = {
    "p1": {"prompt": PROMPT_POSITIVE, "retrieval": True, "reply_facts": False},
    "p2": {"prompt": PROMPT_INTERNAL, "retrieval": True, "reply_facts": True},
    "p3": {"prompt": PROMPT_NO_MATCH, "retrieval": False, "reply_facts": False},
}


def dead_port() -> int:
    """A port nothing is listening on, for case P5.

    Bound and released rather than picked from a range: a hardcoded port that
    something else happens to be using turns the failure-isolation case into a
    test of whatever answered.
    """
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def source_endpoints(*, broken: bool = False) -> dict[str, str]:
    """The three remote catalogue endpoints, in Python-side spelling.

    @param broken - point the first one at a closed local port and leave the
      other two blank. This is case P5: the local corpus and the healthy
      sources have to keep working, the turn has to complete, and the failure
      has to be diagnosable without leaking a credential.
    """
    if not broken:
        return {"hub_endpoint": "", "clawhub_endpoint": "", "skillhub_cn_endpoint": ""}
    return {
        "hub_endpoint": f"http://127.0.0.1:{dead_port()}",
        "clawhub_endpoint": "",
        "skillhub_cn_endpoint": "",
    }


def camel(endpoints: dict[str, str]) -> dict[str, str]:
    """The same endpoints under the names the TypeScript packages read."""
    return {
        "hubEndpoint": endpoints["hub_endpoint"],
        "clawhubEndpoint": endpoints["clawhub_endpoint"],
        "skillhubCnEndpoint": endpoints["skillhub_cn_endpoint"],
    }


def corpus(root: Path | None = None) -> Path:
    """Write the fixture and return the directory to configure as `skills_dir`.

    @param root - where to put the `skills/` directory. Some hosts read the
      skill through a workspace-relative path and an agent that cannot see the
      directory reports the skill as not installed, which reads as a retrieval
      failure and is not one; those hosts pass their own workspace.
    """
    base = Path(tempfile.mkdtemp(prefix="skillsearch-e2e-")) if root is None else root
    skills = base / "skills"
    (skills / SKILL_NAME).mkdir(parents=True, exist_ok=True)
    (skills / SKILL_NAME / "SKILL.md").write_text(SKILL_BODY, encoding="utf-8")
    return skills


# --------------------------------------------------------------------------
# model configuration


class MissingModelConfig(RuntimeError):
    """Raised rather than defaulted: a wrong endpoint fails as a host bug."""


def model_config() -> dict:
    """The model these cases run against, from the environment.

    @returns `base_url`, `model` and `api_key`.
    @raises MissingModelConfig - when the endpoint or model name is unset.
    """
    base_url = os.environ.get("SKILLSEARCH_E2E_BASE_URL", "").strip()
    model = os.environ.get("SKILLSEARCH_E2E_MODEL", "").strip()
    missing = [
        name
        for name, value in (
            ("SKILLSEARCH_E2E_BASE_URL", base_url),
            ("SKILLSEARCH_E2E_MODEL", model),
        )
        if not value
    ]
    if missing:
        raise MissingModelConfig(
            f"set {', '.join(missing)} — see skillcorpus_plugin/tests/host-e2e/README.md"
        )
    return {
        "base_url": base_url,
        "model": model,
        # OpenAI-compatible deployments behind a gateway commonly take any
        # value; the variable is still read so a keyed endpoint works.
        "api_key": os.environ.get("SKILLSEARCH_E2E_API_KEY", "EMPTY"),
    }


def thinking_off() -> dict:
    """`extra_body` that stops a reasoning model spending the whole budget.

    Qwen3.x reasons by default and can spend `max_tokens` doing it, which
    returns an empty assistant message. An empty reply fails these cases for a
    reason that has nothing to do with retrieval, so it is turned off wherever
    the host lets a caller pass `extra_body` through.
    """
    return {"chat_template_kwargs": {"enable_thinking": False}}


# --------------------------------------------------------------------------
# verdicts


def sentinel_in(text: str) -> bool:
    """Whether every fixture fact is present in `text`."""
    return all(fact in (text or "") for fact in SENTINEL_FACTS)


def verdict(
    *,
    case: str,
    mode: str,
    tool_offered: bool,
    auto_channel_filled: bool,
    tool_called: bool,
    delivered: str,
    reply: str,
) -> tuple[bool, dict]:
    """Judge one run the same way on every host.

    Two conditions always hold, whatever the case, and they are the pair that
    makes the modes exclusive:

    - the mode's own channel is the live one — `on_demand` offers the tool,
      `auto` does not;
    - the other mode's channel is inert.

    The rest depends on whether the case expects a retrieval at all. When it
    does, the fixture facts must arrive *through the retrieval channel* — the
    strong condition, and one a reply cannot establish on its own, because the
    body is a file on disk and most hosts give the agent a reader. When it does
    not (P3), no tool call may happen and nothing may be injected.

    @param delivered - everything retrieval put in front of the model this
      turn: the injected block, the tool results, or both.
    @returns the verdict and the fields worth recording in a report.
    """
    spec = CASES[case]
    on_demand = mode == "on_demand"
    facts = {
        "case": case,
        "tool_offered": tool_offered,
        "auto_channel_filled": auto_channel_filled,
        "tool_called": tool_called,
        "sentinel_via_retrieval": sentinel_in(delivered),
        "sentinel_in_reply": sentinel_in(reply),
    }
    ok = tool_offered is on_demand
    if spec["retrieval"]:
        ok = (
            ok
            and auto_channel_filled is (not on_demand)
            and tool_called is on_demand
            and facts["sentinel_via_retrieval"]
        )
        if spec["reply_facts"]:
            ok = ok and facts["sentinel_in_reply"]
    else:
        # Nothing should have happened. A tool call the model made anyway is
        # not a failure on its own — the case is that the answer stayed
        # harmless — but nothing may be injected and nothing may be retrieved.
        ok = ok and not auto_channel_filled and not facts["sentinel_via_retrieval"]
    return ok, facts


def line(mode: str, ok: bool, facts: dict, elapsed_s: float) -> str:
    """One human-readable result line, in the shape all three scripts print."""
    return (
        f"  {facts['case']} {mode:10} tool_offered={facts['tool_offered']!s:5} "
        f"auto_channel={'filled' if facts['auto_channel_filled'] else 'empty '} "
        f"tool_called={facts['tool_called']!s:5} "
        f"via_retrieval={facts['sentinel_via_retrieval']!s:5} "
        f"in_reply={facts['sentinel_in_reply']!s:5} "
        f"{'PASS' if ok else 'FAIL'} ({elapsed_s:.0f}s)"
    )
