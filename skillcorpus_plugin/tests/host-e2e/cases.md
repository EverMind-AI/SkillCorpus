# Cases

Six hosts, two modes, six scenarios. Read [`README.md`](README.md) first — the
corpus, the acceptance conditions, and the verdict vocabulary are defined there
and are not repeated here.

Every prompt below is **verbatim**. Rephrasing one measures a different thing,
and its PASS is not comparable with any other run.

## The host matrix

| Host | Version tested at 0.3.0 | `auto` channel | `on_demand` channel | Real host required | Headless script |
| --- | --- | --- | --- | --- | --- |
| OpenClaw 1.x | 2026.7.1 | `before_prompt_build` hook | native `registerTool` | Yes | [`e2e_openclaw.py --generation 1`](scripts/e2e_openclaw.py) |
| OpenClaw 2.0 | 2026.8.1 | context engine `assemble` | native `registerTool` | Yes | [`e2e_openclaw.py --generation 2`](scripts/e2e_openclaw.py) |
| Hermes | `77ed972` | memory provider `prefetch` | provider `get_tool_schemas` | Yes | [`e2e_hermes.py`](scripts/e2e_hermes.py) |
| Raven | `1cb604a` + local patch | `contributes.context_segments` | `contributes.tools` | Yes | [`e2e_raven.py`](scripts/e2e_raven.py) |
| DeepSeek Harness | `47f9438` | `agent/pre-step` injection | `ctx.tools.register` | Yes | [`e2e_deepseek.py`](scripts/e2e_deepseek.py) |
| WorkBuddy | 5.3.13 | `UserPromptSubmit` hook | local stdio MCP | Yes | No |

### Raven: two hosts, not one

`on_demand` runs on **stock upstream Raven**: `contributes.tools` is an
upstream slot and the plugin subclasses the host's own `Tool`.

`auto` does **not**. It needs `contributes.context_segments`, plus the
`build_plugin_segments` / `AgentLoop(plugin_segments=...)` wiring behind it.
That is a local patch to the Raven checkout and is **not upstream** as of
`1cb604a`. On a stock checkout the case is BLOCKED, not FAIL.

Every Raven row in a report must say which of the two it ran on. Writing a
patched-host `auto` PASS as "Raven supports auto" is the specific error this
section exists to prevent.

### WorkBuddy: why the tool arrives over MCP

WorkBuddy's plugin format declares hooks. A `UserPromptSubmit` hook can inject
text but cannot offer something the model *chooses* to call, and the host
exposes no stable native tool-registration API. What it does have is
`mcpServers` in the plugin manifest, which it merges into its own MCP
configuration at startup — so on-demand mode here is a local stdio MCP server.

MCP is an adapter and nothing more: both paths call the same retrieval,
filtering, deduplication and formatting. A WorkBuddy case that verifies only
the hook log has verified only `auto`.

---

## P1 — Positive retrieval

The main case. Run it for every host, in both modes.

**Prompt**

```text
How do I extract tables from a scanned PDF invoice into CSV?
```

**Expected — `auto`**

- The host's injection channel carries the `pdf-tables` body — the fixture
  facts are present in the injected text.
- `skill_search` is absent from the tool surface the model sees.
- No tool call happens.

**Expected — `on_demand`**

- Nothing is injected on this turn or any other.
- `skill_search` is on the tool surface.
- The model calls it, unprompted, with a query about extracting tables from a
  scanned PDF — the query wording is recorded, not asserted.
- The tool returns `pdf-tables`, and its result carries the fixture facts.

**What P1 does not require: that the reply repeats the facts.** It was written
that way and the first run showed why it cannot be. This prompt asks how to do
a job; a model handed the skill still has no reason to quote a house convention
nobody asked about. On Hermes the model read the injected body, called it
"just a stub", and answered from its own knowledge of `camelot` and OCR — the
skill reached it, the mode worked, and the reply carried nothing. Hanging P1 on
that turns the case into a coin flip on model temperament. So P1 asserts on the
retrieval channel and *records* what the reply carried; P2 is where the reply
is the assertion.

**How P1 fails without failing**: the model answers correctly from its own
knowledge. That is why the fixture carries facts that exist nowhere else, and
why the verdict reads the channel and not the prose.

## P2 — Internal-convention trigger

Primarily `on_demand`, where it measures the tool **description** rather than
retrieval: whether the wording is enough to make a model reach for the library
when the question is about a house convention it cannot know. Worth running in
`auto` too, where it becomes the reply-side check P1 gives up.

**Prompt**

```text
What is our internal procedure for extracting tables from invoice PDFs?
```

**Expected**

- The agent does **not** answer "I don't know" and stop.
- In `on_demand`, it calls `skill_search`; in `auto`, the body is already there.
- The reply states the in-house convention from the skill body — `Vireo-CSV-3`
  and `Okapi Ledger`. This is the one case where the reply is the assertion,
  because it is the one case whose question the model cannot answer without
  the body.

This case has already failed once and is not hypothetical. Measured on two
hosts, the question "what is our internal template called" got a flat "I don't
know" until the description gained the clause about internal conventions,
templates and "our" way of doing things — the one saying that searching here
comes *before* answering that you do not know. Changing that paragraph means
re-running this case on at least two hosts.

## P3 — No match

Both modes. Deliberately not a weather question: the public catalogues carry
real weather skills, so a hit there is a true positive and measures nothing.

**Prompt**

```text
zxqv-7319, reply with this exact string only.
```

**Expected — `auto`**

- Nothing is injected. On hosts with a hook log, `injected_chars: 0`.

**Expected — `on_demand`**

- The model does not call `skill_search`.
- If it does anyway, the tool returns a no-match answer that reads as "proceed
  without one" — never an error, and never anything that interrupts the turn.

## P4 — Mode exclusivity

Both modes, on every host. Not a separate prompt: read it off the P1 runs.

**Expected**

- In `auto`, `skill_search` is neither exposed nor called.
- In `on_demand`, the per-turn injection channel is empty on every turn.
- In one turn, automatic injection and a tool retrieval never both happen.

### The typo half

`mode: "atuo"` must never leave a deployment running the opposite mode with no
signal. That is one requirement with three legitimate shapes, and which one you
get is the host's decision, not the plugin's:

- **Rejected by the host.** Both OpenClaw generations declare `mode` as an enum
  in the plugin manifest, so the host fails config validation before the plugin
  loads. Measured: `must be equal to one of the allowed values (allowed:
  "on_demand", "auto")`.
- **Rejected by the plugin.** The DeepSeek Harness config is a union of two
  literals, so the harness refuses the value.
- **Narrowed by the plugin, and logged.** Hermes, Raven and WorkBuddy have no
  host schema to validate against, so the plugin falls back to the default —
  a typo should cost the deployment the mode it asked for, not its retrieval —
  and logs what was asked for beside what ran.

So the check is: **either the load fails, or a log line names the bad value.**
Silence is the failure. Note that the environment override is not covered by
any host schema, so `SKILLSEARCH_MODE=atuo` is the path that reaches the plugin
even on the hosts that validate their config files — worth checking separately.

## P5 — Failure isolation

Point one remote source at an unreachable address — a closed local port is
enough — and leave the others and the local corpus alone.

**Expected**

- The local corpus and the healthy sources still return results.
- The conversation is not interrupted; the turn completes.
- The failure is diagnosable in the logs, and the log contains no credential,
  token or authorization header.

Retrieval fails open by design: a broken source and an empty result reach the
model the same way, as "carry on". Which is exactly why the third condition
matters and is the one that has failed. Both engines *report* a failing source;
whether an adapter **consumes** that report is per-package, and the two
OpenClaw packages did not — so an unreachable catalogue and an empty one looked
identical from outside. Run this per host, not per engine: the engine is shared
and the wiring is not.

Every script takes `--broken-source`, which points one catalogue at a port it
just closed. A hardcoded port that something happens to be using would turn
this into a test of whatever answered.

## P6 — Restart and upgrade

**Expected**

- After installing and restarting the host, the plugin is still loaded and its
  configuration is intact.
- Upgrading 0.2.0 → 0.3.0 changes the effective default to `on_demand`, on a
  configuration that names no mode. Verify by *observing* the default, not by
  reading the schema — `e2e_deepseek.py default` writes no `mode` key for
  exactly this.
- Setting `mode: auto` restores the 0.2.0 behaviour.
- On hosts driven by a script, `--restart` is the check: the same turn twice
  against the same profile directory, nothing rewritten in between, a fresh
  session id each time so the second turn inherits none of the first one's
  answers. It is judged the same way as the first run, so "still works" means
  the same thing both times rather than "did not crash".
- WorkBuddy must be verified through a standard marketplace install. Editing
  `settings.json`, `installed_plugins.json` or `known_marketplaces.json` by
  hand, or running the bundle directly, tests the bundle and not whether the
  host loaded it.

---

## WorkBuddy, in detail

The rest of the hosts differ only in which channel carries what. WorkBuddy has
a second process, so it gets its own list. Both lists are run against a
marketplace install of the commit under test.

### `on_demand` (the default)

1. WorkBuddy restarts and the plugin is still enabled.
2. The MCP server initialises — no failed entry, no restart loop, no repeating
   error in the MCP log.
3. `tools/list` puts `skill_search` in front of the model.
4. The P1 prompt makes the model call it, on its own.
5. The tool returns `pdf-tables`.
6. The `UserPromptSubmit` hook injected nothing on that turn.

### `auto`

1. The MCP process is **alive and healthy**, and its tool list is empty.
2. The hook retrieves and injects `pdf-tables`.
3. No `skill_search` call happens.
4. No failed MCP entry, restart loop, or persistent error log.

Point 1 in the `auto` list is a regression test with a known cause. Through
`e337cfa` the entry point served stdin only in on-demand mode, so with
`mode: auto` the declared server started and exited 0 immediately. The manifest
cannot withdraw a statically declared MCP server when a user picks `auto`, so
the host kept launching one that died — a failed entry, or restart churn, next
to a hook path that was working fine. The server now serves in both modes and
answers `tools/list` with an empty array in `auto`. "Process alive, tool list
empty" is the assertion; "process exits" is the bug.

The older WorkBuddy verification notes checked the hook log, which is an
`auto`-mode check. It is not a completion standard for the default.

---

# S1–S9 — the shared skills library

A second family of cases, from `skillsearch-shared-skills-spec.md`. They are
numbered as that document numbers them, so a result here can be read against
it line by line.

These differ from P1–P6 in what they are about. P1–P6 ask whether *one* host
retrieves correctly; these ask whether the hosts can see **each other**, which
means at least two of them have to be running and the evidence lives in files
on disk rather than in one turn's transcript.

## The corpus

Three fixtures, and the reason there are three rather than one is a mistake
worth not repeating. They must not compete: the hosts run with `topK: 1`, so
two skills on the same subject means the case measures which one ranked higher
rather than what it set out to measure.

| Fixture | Lives in | Facts | Used by |
| --- | --- | --- | --- |
| `invoice-audit` | host A's own directory | `Wombat-Ledger-7`, `Tapir Threshold` | S4 |
| `rotate-signing-keys` | the shared directory | `Narwhal-KMS-4`, `Quokka Cutover` | S5, and the per-host probe |
| whatever the catalogue returns for "extract tables from a PDF" | installed by retrieval | its own frontmatter name | S1, S2, S3, S7, S8 |

## The cases

| # | The claim | How it is verified | Script |
| --- | --- | --- | --- |
| S1 | a retrieved skill appears under `<shared root>/skills/` | install for real from EverMind SkillHub, then read the ledger | `e2e_install.py` |
| S2 | the next turn finds it locally, **exactly once**, no restart | retrieve twice on one engine, count the heading; then again on a freshly built engine | `e2e_install.py` |
| S3 | agent B retrieves what agent A installed | Raven installs from the catalogue; OpenClaw — other host, other language port, own process — is then asked | `e2e_shared.py` |
| S4 | a skill in A's own directory is retrievable in B | Raven registers its directory; OpenClaw is asked the question only that skill answers | `e2e_shared.py` |
| S5 | a skill dropped in by hand reaches the hosts next turn, no restart | write into the shared directory mid-run, ask again | `e2e_shared.py`, `e2e_shared_hosts.py` |
| S6 | `enabled: false` hides A from B, **and survives A restarting** | edit the registry, ask B, re-register A, read the flag back | `e2e_shared.py` |
| S7 | uninstall → directory gone, **a record kept**, not retrievable | remove through the Python port, observe from the TypeScript host, read `uninstalled.log` | `e2e_shared.py`, `e2e_install.py` |
| S8 | a failed update leaves the previous version working | install for real, then update to an absent version from a dead endpoint | `e2e_install.py` |
| S9 | a corrupt registry costs sharing, not retrieval | write `{ this is not json` and ask again | `e2e_shared.py` |

`e2e_shared_hosts.py` runs the narrower question — *does this host join at
all* — separately against each of the five headless hosts, because the
registration is wired at six different call sites and a fix applied to one is
not a fix applied to the others. Three bugs on this branch were exactly that.

## Two things about judging these

**Registration and retrieval are separate verdicts.** A host that registers but
whose model never called `skill_search` has not failed — in on-demand mode the
model decides, and one that answers from memory leaves the wiring untested
rather than broken. That is reported as INCONCLUSIVE. A host that does not
register *is* a failure: the others then cannot see it, which is half the
feature. Both OpenClaw generations also get an engine probe — the plugin's own
`buildEngine`, no model in the loop — so the wiring verdict cannot come out
inconclusive at all.

**A catalogue that answered with nothing is not a failed install.** Retrieval
fails open, so an unreachable service and an empty result look identical from
outside. The install cases report BLOCKED in that situation rather than a red
that a rerun clears.

## WorkBuddy, by hand

The one host with no headless path, so S1–S9 are steps rather than a script.
Nothing here is exotic — it is the same corpus and the same questions the
scripts use, driven through the UI.

**Both of its paths reach the shared library, and they have different
lifecycles.** `UserPromptSubmit` is a fresh process every turn; the MCP server
starts with the session and lives. Both call `scanDirs`, so both register —
which is why step 2 exists: on the hook path "at startup" means "every turn",
on the turn's hot path, inside an 8-second budget.

### Setup

```bash
mkdir -p ~/.evermind-skillsearch/skills/rotate-signing-keys
cat > ~/.evermind-skillsearch/skills/rotate-signing-keys/SKILL.md <<'MD'
---
name: rotate-signing-keys
description: Rotate the service signing keys and re-issue downstream credentials safely.
---

House procedure: stage the new key under the `Narwhal-KMS-4` alias and keep the
previous one live until the `Quokka Cutover` window closes.
MD
```

Then start WorkBuddy and run one ordinary turn, so the plugin loads.

### Steps

1. **It registered.** `cat ~/.evermind-skillsearch/registry.json` — there is a
   `workbuddy` entry, `enabled: true`, and `dir` is WorkBuddy's real skills
   directory as an absolute path. If the entry is missing, stop: without it the
   other agents cannot see this one, which is half the feature.

2. **It does not rewrite the registry every turn.** Note the file's mtime, run
   three or four more turns, check it again. Unchanged. This is the short
   circuit the per-turn hook depends on — a write per turn is both a cost on
   the hot path and a race with four other agents.

3. **S5 — the shared skill is retrievable.** Ask *"What is our internal
   procedure for rotating signing keys?"* in a fresh task. In the default
   `on_demand` mode the agent should call `skill_search`; the answer must
   mention `Narwhal-KMS-4` and `Quokka Cutover`, which exist nowhere else. If
   the model answers "I don't know" without calling the tool, that is
   INCONCLUSIVE rather than a failure — rerun it; see the verdicts in
   `README.md`.

4. **S4 — the other direction.** Put a skill in WorkBuddy's *own* skills
   directory, then open another agent that has this plugin and ask for it
   there. It should be found without that agent being told anything.

5. **S6 — the user's switch holds.** Set `enabled: false` on the `workbuddy`
   line in `registry.json`. The other agent stops finding WorkBuddy's skills.
   Restart WorkBuddy, then read the file again: **still `false`**. A host
   re-registering must never undo this, or the file is not editable.

6. **S9 — a broken registry costs sharing, not the turn.** Replace
   `registry.json` with `{ this is not json`, then ask anything. The turn
   completes normally and retrieval still works from WorkBuddy's own
   directory. Restore the file afterwards.

7. **S7 — removal leaves a record.** After anything has been installed by
   retrieval, remove it and check `~/.evermind-skillsearch/uninstalled.log`:
   one JSON line per removal, with the origin, the version and a timestamp.

### What to record

Host version, plugin version, the commit, and for each step what you saw —
following the same fields as the other reports. A step that could not be run
is written down with the reason rather than left out.
