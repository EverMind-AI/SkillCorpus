# Host end-to-end tests

The plugin packages have their own suites and CI runs them on every push. Those
suites prove the plugin's own logic. They cannot prove that a host loaded the
plugin, that the mode the user configured is the mode that ran, or that a skill
reached the model — and every defect this family has shipped so far was in that
gap.

This directory is that second layer: fixed prompts, a fixed corpus, and a fixed
way of deciding PASS, so a result from one release is comparable with the next.

- [`cases.md`](cases.md) — the cases. Six hosts, two modes, six scenarios.
- [`reports/`](reports) — what actually happened, one file per release.
- [`scripts/`](scripts) — the three hosts that can be driven headlessly.

## Three layers, and what each one is worth

| Layer | Where | What a pass means |
| --- | --- | --- |
| Unit / contract | `*/test`, `*/tests`, run by CI | The plugin's own logic is right, against types and fakes copied from the host |
| Spawned process | `plugin-workbuddy/test/mcp.test.ts` | The shipped bundle really speaks the protocol, in a real child process |
| Host E2E | here | A real host loaded the plugin and a real model saw the skill |

A spawned-process test is **not** a host test. It proves the bundle answers
JSON-RPC; it says nothing about whether WorkBuddy launches it, or with what
arguments. Do not report one as the other — the report template has separate
lines for them for that reason.

## The corpus

One skill, written to a temporary directory and named as `skills_dir` in the
plugin config. [`scripts/_e2e.py`](scripts/_e2e.py) writes it; for a manual host
you write the same file by hand:

```markdown
---
name: pdf-tables
description: Extract tables from PDF documents, scanned or native, into CSV.
---

Use camelot for native PDFs. OCR scanned pages before extracting tables.

In-house convention: write the extracted CSV through the `Vireo-CSV-3` profile
and title the summary sheet `Okapi Ledger`.
```

The first paragraph is what makes the corpus retrievable: the description is
what the query is matched against. The second paragraph is what makes the
result *readable*. `camelot` and "OCR first" are things a competent model
volunteers unprompted, so a reply containing them proves nothing;
`Vireo-CSV-3` and `Okapi Ledger` exist nowhere outside this repository, so a
reply carrying them can only have come from the body.

Two rules about the corpus, both learned the hard way:

- **Facts, not instructions.** A skill body that says "reply with FOO" is read
  by a 2.0-generation host's model as prompt injection and refused, and the
  case then fails for a reason that has nothing to do with retrieval. State
  facts and ask a question they answer.
- **Put it where the agent can see it.** On hosts that give the agent a
  workspace, a corpus in a stray temp directory reads as a skill that is not
  installed: the agent checks, does not find the directory, and says so. Write
  it inside the session workspace.

## Turning the remote catalogues off

From 0.2.0 the three remote sources ship **enabled**. A test that leaves them
alone is measuring what a public catalogue returned this minute, and the
negative case will find a real weather skill. Blank all three in the config
under test — `hub_endpoint`, `clawhub_endpoint`, `skillhub_cn_endpoint` on the
Python hosts, `hubEndpoint`, `clawhubEndpoint`, `skillhubCnEndpoint` on the
TypeScript ones — except in the failure-isolation case, which is about exactly
this.

## Acceptance, per mode

The two modes are exclusive, and each one's acceptance includes the other one
being inert. That is the whole point of the pair.

**`auto`** — retrieval runs on the host's own per-turn hook, without the model
choosing anything:

1. the injection channel carries the skill body (hook, context segment,
   `prefetch`, pre-step — whichever this host uses), fixture facts and all;
2. `skill_search` is **not** on the tool surface the model sees;
3. no tool call happens.

**`on_demand`** (the default from 0.3.0) — nothing is injected and the model
decides:

1. `skill_search` **is** on the tool surface;
2. the injection channel stays empty on every turn;
3. the model calls the tool, with a query related to the task;
4. the tool's result carries the fixture facts.

Condition 3 in each list is the one that gets skipped, and it is the one that
matters: a tool that is offered and never called is a tool description problem,
not a success.

### Two things the fixture facts can prove, and only one of them per case

The skill body is a file on disk and most hosts give the agent a file reader,
so a reply carrying the fixture facts does not prove retrieval delivered them.
Every case therefore asserts on **the retrieval channel** — the injected text,
or the tool's return value.

The reply is the other half, and it is deliberately *not* a P1 condition. P1
asks how to do a job; a model that has just been handed the skill still has no
reason to quote a house convention nobody asked about, and one of them read the
injected body, called it "just a stub" and answered from its own knowledge. The
mode worked; the prose said nothing. P2 is the case whose question cannot be
answered without the body, and that is where the reply is the assertion.

The scripts record both separately — `sentinel_via_retrieval` and
`sentinel_in_reply` — in every case, and only weigh the second in P2. A manual
run must say which one it observed.

## Before you start: record the environment

A result without these is not reproducible and should not go in a report.

```text
Plugin version:            (from the package under test)
SkillCorpus commit:        (git rev-parse HEAD — the exact SHA)
Host and version:          (release string, or checkout commit)
Install method:            (the actual command or UI path)
Test date, tester:
OS, Node/Python versions:
Remote sources:            enabled / disabled
Model and gate config:     (route name, not the endpoint — see redaction)
```

## Running the automated three

Hermes, Raven and the DeepSeek Harness can be driven headlessly, so
[`scripts/`](scripts) has one script each. They take the model from the
environment — never from a committed constant, which would be both wrong for
the next maintainer and an internal address in a public repository:

```bash
export SKILLSEARCH_E2E_BASE_URL=https://your-openai-compatible-endpoint/v1
export SKILLSEARCH_E2E_MODEL=your-model-id
export SKILLSEARCH_E2E_API_KEY=...          # optional, defaults to EMPTY

python scripts/e2e_hermes.py   --host /path/to/hermes-agent --prefetch-budget 300
python scripts/e2e_raven.py    --host /path/to/raven --plugin-site /path/to/site-packages
python scripts/e2e_deepseek.py --host /path/to/deepseek-harness auto on_demand default
```

Each prints one line per mode and exits non-zero on any failure; `--dump FILE`
writes the full record, including what went over the wire.

Two things to know before reading their output:

- **Raven `auto` reports BLOCKED on stock Raven**, because the
  `context_segments` plugin slot it needs is not upstream. The script asks the
  host for the symbol rather than trusting a version string, so it will start
  reporting a real result the day the slot lands. See `cases.md`.
- **Hermes caps external `prefetch` at 8 seconds**, in the host, whatever the
  plugin config asks for. The rewriter is one model call and against a
  reasoning deployment it alone spends more than that, so `auto` under the host
  default delivers nothing. `--prefetch-budget` raises the cap for the run and
  `--no-rewrite` removes the call; a report must say which was used, because
  "auto passes" means a different thing in each case.

OpenClaw 1.x, OpenClaw 2.0 and WorkBuddy have no headless path — 2.0's context
engine needs four capability gates and a running gateway, WorkBuddy is a
desktop application installed from a marketplace. Those are Markdown steps in
`cases.md`, executed by hand. Do not add a script that cannot run.

## Verdicts

| Verdict | Meaning |
| --- | --- |
| PASS | Every condition for that mode held, observed directly |
| FAIL | The plugin did not do what the case requires |
| BLOCKED | The host cannot run this case — a missing slot, an unavailable build, a gate nobody can grant. Say what would unblock it |

BLOCKED is not a soft FAIL and must never be written as PASS. Raven `auto` on
stock Raven is BLOCKED; recording it as "Raven supports auto" is the specific
mistake this row exists to prevent.

An untested case is written as "not executed", with the reason. A report whose
gaps are visible is worth more than one that looks complete.

## Redaction

**Never commit**: API keys, tokens, cookies, authorization headers; internal
endpoints, hostnames or IPs; real home directories or usernames; unredacted
chat transcripts; skill content containing user data; bulk host logs, caches or
recordings; screenshots whose origin and commit cannot be stated.

**Do commit**: redacted log fragments, small fixed fixtures, tool-call names
with redacted arguments, the skill IDs that came back, reproducible commands,
and an explicit PASS/FAIL. Markdown evidence beats a screenshot.

Endpoints go in reports as a description — "an internal OpenAI-compatible
gateway, Qwen3.6-27B" — never as a URL. The scripts take theirs from the
environment for the same reason.
