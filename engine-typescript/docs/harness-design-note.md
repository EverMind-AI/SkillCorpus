# Agent Note: Per-turn skill retrieval ships as a pre-step plugin

> Written as a DeepSeek Harness Agent Note, kept here with the package it
> documents. Copy it back into `.agents/notes/implemented/architecture/`
> when installing into a harness checkout.

*Counterpart: [中文](harness-design-note.zh.md).*

## Problem

`dsh-tool-skill` publishes every skill's name and description in a tool schema and lets the model load one by name. That works while the catalog is small and the model can recognize the right name from a one-line description. It stops working at a catalog of thousands: the schema no longer fits, and the model has to know a skill exists before it can ask for it.

A deployment with a large corpus wants the opposite direction — search the corpus against what the user just wrote, and put the matching bodies in front of the model without a round trip. The retrieval quality that makes this usable is not one ranking function; it is a rewriter that decides whether the turn wants skills at all, several sources ranked independently and fused, and a model call that rejects what survived ranking but that this agent cannot execute.

## Decision

`@deepseek-ai/dsh-skill-search` is a function plugin on `agent/pre-step`. It calls `next()` first, returns a `reject` decision untouched, and otherwise appends one user message to `decision.messages`. Nothing else in the loop changes.

Retrieval is: rewrite (one model call, which may answer `need_retrieval: false` and skip the rest), fan out across sources, fuse by weighted Reciprocal Rank Fusion at K = 60, hydrate bodies for candidates that arrived as catalog metadata, gate, render.

**Fusion ranks by position, not by score.** A local BM25 score and a catalog quality score are not comparable numbers, so the merge uses each hit's rank within its own source. This is what makes the gate mandatory rather than an optimization: every source's best hit reaches the shortlist however weakly it matched, so without the gate "what's the weather" injects whatever the local directory ranked first. The gate is also the only step that can see a skill is *unexecutable here* — a body that assumes a vendor API, a `{baseDir}` placeholder, or a slash-command dispatcher — which no ranking function can detect. It receives the agent's tool names from `ctx.get('tools').schemas(agent)` for exactly that check, and is instructed that selecting nothing beats selecting something irrelevant.

**Every step degrades toward fewer skills, never toward a failed turn.** A source that throws contributes an empty list; a gate that times out or replies unparseably keeps its top candidates; `retrieve` itself catches and returns `''`. Retrieval runs before the model answers the user, so its worst case must cost the turn its skills and nothing else.

The rewriter and the gate reach the model through `ctx.llm.stream` with an explicit `provider`/`model` pair, configured together or not at all. Unset, retrieval runs unfiltered and injects the top `topK` by rank — a documented degraded mode, not a silent default route. A terminal finish (including `max-tokens`, because a truncated reply carries truncated JSON) rejects, which each caller turns into its own fallback.

The injected message declares a `skill-search` member on `MessageSourceMap`, carrying `form: 'instructions'` — the bodies are third-party text the model is expected to follow — and the injected ids, so a transcript consumer reads what was shown from metadata instead of re-parsing model-facing text.

This plugin and `dsh-tool-skill` are alternatives, not layers: running both publishes the same skills twice, once as a schema and once as text. A deployment mounting this one disables that one.

## Alternatives considered

- **Extend `dsh-tool-skill` with a `find_skill` tool** — rejected: it keeps the round trip, and the model still has to decide to search. The whole point of a large corpus is that the model does not know what is in it.
- **Rank without the gate, inject the top K** — rejected: measured directly. Position-based fusion guarantees each source contributes its best hit, so an unrelated turn reliably injects an unrelated skill.
- **Add `'skill-search'` to `GenerateOptions.purpose`** — rejected: `purpose` has exactly two consumers, both DeepSeek-adapter behaviors (a compaction header, disabled thinking for titles). A third value with no consumer is an unowned public choice; these requests leave it unset.
- **Register the sources through the `dsh-skill` provider registry** — rejected: that registry answers "list and load skills by name" for the catalog path. Retrieval needs per-source ranked lists with scores and rank positions, which is a different operation set; sharing one interface would have bent both.

## Consequences

- A deployment with a large skill corpus can inject relevant bodies per turn without publishing a catalog the model must first read.
- The KV-cache prefix before the injection stays reusable; the suffix from the injected message onward changes whenever the selection changes.
- Two auxiliary model calls per retrieving turn, outside the conversation.
- 23 package tests cover the tokenizer, BM25, fusion (position over score, cross-source lift, name collision), the gate (selection, empty selection, tool listing, fallback on transport failure, fenced replies), the rewriter (no-retrieval verdict, unparseable reply, blank query), the local scanner, and the engine end to end.

## Known gaps

Named in [the package README](../README.md#known-limitations-and-deferred-work): retrieval decisions are not session events, the local scan is cached for the process lifetime, and one route serves both model calls. There is no keyless snapshot through a real runnable example yet — the plugin is not in a shipped bundle, and adding one is the next step for a deployment that mounts it.
