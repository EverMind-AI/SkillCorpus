# skillsearch

Skill retrieval for agent hosts, in two runtimes.

An agent host wants to answer one question before every turn: *given what the user just said, which skills should the model see?* Both implementations here answer it the same way, and produce the same injected text.

```
query
  ├─ rewrite          turn the message into a retrieval query   (optional)
  ├─ fan out          local BM25 · remote catalog · agent recall
  ├─ fuse             weighted RRF (K = 60), deduplicated
  ├─ hydrate          fetch bodies for metadata-only hits
  ├─ gate             drop what this agent cannot run here      (optional)
  └─ render           resolve directories and links to real paths
→ text to inject
```

| | [`python/`](python) | [`typescript/`](typescript) |
| --- | --- | --- |
| Hosts | raven, hermes, OpenClaw, or any host over HTTP | DeepSeek Harness |
| Entry | `SkillSearch.retrieve(query) -> str` | `agent/pre-step` waterfall, or `SkillSearchEngine` directly |
| Install | `pip install -e python` | copy into `packages/skill/skill-search/` |
| Host changes | raven needs the patch in `python/host-patches/` | none |

The two are ports of one design, not a shared core with bindings: each is idiomatic in its own runtime and neither imports the other. The prompts are byte-identical, the fusion constant and tokenizer are the same, and the same query over the same skills directory selects the same skills.

## What both guarantee

**Configure a model if you can.** Fusion ranks by position, not by score — that is what lets sources with different scoring scales be compared — so a source's best hit enters the shortlist even when it is a weak match. Filtering those out is the gate's job, and without a model there is no gate: ask about the weather with a PDF skill installed and the PDF skill still shows up.

**Retrieval never raises.** It sits on the turn's hot path in every host, so a failure returns nothing to inject — the turn loses its skills, not its response.

**Capability is presence, not flags.** No catalog endpoint means no remote source; no model means no rewrite and no gate. A configuration can never say two contradictory things.

Per-runtime configuration, extension points, and known gaps live in each directory's own README.
