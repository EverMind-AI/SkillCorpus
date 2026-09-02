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

This is why the two modes are a `union` of two literals and not a free string:
a typo like `"atuo"` must be rejected at config load, not silently downgraded
to the default. Check the host's error surface for the typo, once per host.

## P5 — Failure isolation

Point one remote source at an unreachable address — a closed local port is
enough — and leave the others and the local corpus alone.

**Expected**

- The local corpus and the healthy sources still return results.
- The conversation is not interrupted; the turn completes.
- The failure is diagnosable in the logs, and the log contains no credential,
  token or authorization header.

Retrieval fails open by design: a broken source and an empty result reach the
model the same way, as "carry on".

## P6 — Restart and upgrade

**Expected**

- After installing and restarting the host, the plugin is still loaded and its
  configuration is intact.
- Upgrading 0.2.0 → 0.3.0 changes the effective default to `on_demand`, on a
  configuration that names no mode. Verify by *observing* the default, not by
  reading the schema — `e2e_deepseek.py default` writes no `mode` key for
  exactly this.
- Setting `mode: auto` restores the 0.2.0 behaviour.
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
