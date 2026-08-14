# skillsearch

Skill retrieval for agent hosts. One engine, three adapters.

An agent host wants to answer one question before every turn: *given what
the user just said, which skills should the model see?* This package
answers it — searching a local skills directory, a remote catalog such as
[SkillHub](https://evermind.ai/skillhub) (the hosted endpoint over
[SkillCorpus](https://github.com/EverMind-AI/SkillCorpus)'s 96,401 vetted
skills), and the agent's own accumulated skills, fusing the results,
narrowing them with a model, and returning the text to inject.

```python
from skillsearch import SearchConfig, SkillSearch

search = SkillSearch(SearchConfig(skills_dir="~/.agent/skills"))
block = await search.retrieve("extract tables from a scanned PDF invoice")
```

That is the whole interface. Everything else is either configuration or an
adapter that calls it at the right moment.

## Install

```bash
pip install -e .
```

Python 3.11+. One runtime dependency (`httpx`), and only when a remote
catalog is configured.

## What it does

```
query
  ├─ rewrite          turn the message into a retrieval query   (optional)
  ├─ fan out          local BM25 · remote catalog · agent recall
  ├─ fuse             weighted RRF, deduplicated
  ├─ hydrate          fetch bodies for metadata-only hits
  ├─ gate             drop what this agent cannot run here      (optional)
  └─ render           resolve {baseDir} and links to real paths
→ text to inject
```

The optional steps degrade to no-ops. Configure nothing but a skills
directory and you still get local retrieval and a rendered block.

**Configure a `model` if you can.** Fusion ranks by position, not by
score — that is what lets sources with different scoring scales be
compared — so a source's best hit enters the shortlist even when it is a
weak match. Filtering those out is the gate's job, and without a model
there is no gate: ask about the weather with a PDF skill installed and the
PDF skill still shows up.

**`retrieve` never raises.** It sits on the turn's hot path in every host,
so a retrieval failure returns `""` — the turn loses its skills, not its
response.

## Configuring

Every field of `SearchConfig` has a default that does something sensible,
and capability is expressed by presence rather than by flags: no
`hub_endpoint` means no remote source, no `model` means no gate. A config
can never say two contradictory things.

```python
SearchConfig(
    skills_dir="~/.agent/skills",
    hub_endpoint="https://skillhub.evermind.ai",   # omit → local only, see below
    model="gpt-4o-mini",                           # omit → no rewrite, no gate
    top_k=5,
    max_select=2,
)
```

Hosts hand over their own config dict; `SearchConfig.from_mapping` coerces
strings and ignores keys it does not know, so a host can pass a whole slice
without filtering it first.

`hub_endpoint` is any service speaking the three-tier catalog API —
`GET /openapi/v1/skills?q=`, `/skills/{id}`, `/skills/{id}/download`, each
answering `{error, requestId, status, result}` with `status == 0` for
success. [SkillHub](https://evermind.ai/skillhub) is a public one, serving
the [SkillCorpus](https://github.com/EverMind-AI/SkillCorpus) corpus —
96,401 skills, each carrying its upstream license, retrieval quality
measured in the [corpus paper](https://arxiv.org/abs/2607.15557) — or run
your own. Leave it unset and everything else works against a local
directory.

Both model calls are bounded, because both sit between the user's message
and the model's reply: `rewrite_timeout_s` (5s) and `gate_timeout_s` (20s).
A timeout degrades — the raw query is searched, the top hits are injected —
rather than delaying the turn.

## Beyond retrieve

Four methods for what a running host needs after startup.

```python
search.has_sources          # anything configured to search? install the hook or don't
search.invalidate()         # a SKILL.md changed on disk; rescan on the next search
search.set_provider(p)      # a /model switch happened; move both model calls with it
await search.aclose()       # release the HTTP pool this object built
```

`invalidate` matters more than it looks: the local scan is cached for the
life of the object, so without it a skill written at runtime stays
invisible until restart. `set_provider` likewise — the rewriter and the
gate hold the provider they were built with, so a switch away from a dead
credential fixes the conversation and leaves retrieval calling the old one.

A host that already has a catalog client should pass it as `hub_client=`
rather than let a second one be built: one connection pool, one catalog
configuration, and `aclose` leaves an injected client alone.

## Hosts

| Host | How it plugs in | Status |
|---|---|---|
| **Raven** | context segment claiming the `skills` stage | needs a host-side change, see below |
| **Hermes** | context engine, via the per-turn `prefetch` hook | adapter written, not yet packaged |
| **OpenClaw** | HTTP call from `before_prompt_build` (TypeScript host) | server ready, TS plugin not written |

**Raven needs a host-side change first.** Its plugin system contributes
memory backends and tools; a prompt-assembly stage is neither, so
`context_segments` has to be added to Raven before this plugin can attach.
Until then `pip install` succeeds and the plugin is silently ignored.
[`docs/adapters.md`](docs/adapters.md) lists the four files involved and
gives a one-line check.

Full wiring for each host, including manifests and config placement, is in
that same document.

## Layout

```
skillsearch/
├── engine.py        the entry point — SkillSearch.retrieve()
├── config.py        SearchConfig: one shape, however a host spells it
├── ports.py         what the engine needs from a host, as protocols
├── router.py        fan-out across sources
├── fusion.py        weighted reciprocal-rank fusion
├── gate.py          LLM relevance + environment filter
├── rewriter.py      query rewriting
├── local_pool.py    BM25 over local skills
├── local_store.py   SKILL.md scanner, for hosts without one
├── refs.py          {baseDir} and link resolution
├── hub_client.py    remote catalog client
├── sources/         local · hub · memory-recall
└── adapters/        raven · hermes · http_server
```

Nothing under `skillsearch/` imports a host — that is what keeps the core
portable, and an `import raven` anywhere outside `adapters/` is a bug.

## Known limitations

- **The rewriter's `need_retrieval` verdict costs recall, measurably.**
  Probed against a live model over six runs, one query — *"a test passed
  last week and fails now, which change broke it"* — flipped between
  `true` and `false` roughly half the time, and a `false` drops a skill
  that ranking and the gate would both have selected. The prompt already
  says "when in doubt, choose retrieval"; the model does not always agree.
  A deployment that would rather pay for the fan-out than miss a skill
  should set `rewrite=False` and let the gate narrow on its own.
- **A remote bundle's own files land in the cache, not next to the skill.**
  `resolve_refs` rewrites `{baseDir}` to the extracted directory, so a
  skill that ships scripts works — but only after `install`, which happens
  post-gate, for selected hits.
- **The local scan caches for the life of the object.** Call `invalidate()`
  when skills change on disk; nothing watches for you.

## License

Apache-2.0. `bm25.py` is vendored from
[Raven](https://github.com/EverMind-AI/Raven) (same license).

If skill retrieval over SkillCorpus is part of your work, please cite the
corpus paper — the BibTeX is in the [repository README](../README.md#citation).
