# Changelog

## Unreleased

### Added

- **`INSTALL.agent.md`** — an installation playbook written for the agent
  itself: paste one prompt from the README and the host installs, verifies
  (a positive and a negative retrieval probe) and reports. Carries hard
  rules — back up configs, show diffs before applying, never overwrite a
  file that fails to parse — and an uninstall section.
- **A Chinese README** (`README.zh.md`), mirroring the English one.

- **A TypeScript implementation** under [`engine-typescript/`](engine-typescript), for
  DeepSeek Harness. Both implementations now sit side by side and the
  Python one moved into [`engine-python/`](engine-python); `pip install -e .` becomes
  `pip install -e engine-python`.
- **`rewrite_timeout_s`** (Python, default 5s) and **`rewriteTimeoutMs`**
  (TypeScript, default 5s) bound the rewrite call.
- **`hub_download_timeout_s`** (default 30s) bounds a bundle download
  separately from a catalog query.
- **`SkillSearch.invalidate()`**, **`SkillSearch.set_provider()`** and
  **`SkillSearch.has_sources`** — the file-watcher, `/model`-switch and
  is-anything-configured paths a running host needs.
- **`hub_client=`** on `SkillSearch`, so a host can hand over the catalog
  client it already built rather than have a second one created.
- **`{baseDir}` and bundled-file link resolution in TypeScript**
  ([`refs.ts`](engine-typescript/src/refs.ts), a port of `refs.py` verified
  byte-identical on shared fixtures), behind a `resolveRefs` config
  defaulting on. Previously TypeScript injected the placeholders literally —
  and the gate prompt reads a literal `{baseDir}` as proof a skill cannot
  run, so local skills that shipped files were penalized on one side only.
- **`hubMinSafety`** in the TypeScript config, matching Python's
  `hub_min_safety`; catalog searches also now pass `limit` explicitly
  instead of slicing the default page.

### Fixed

- **A zip that understates an entry's size is now stopped while inflating**,
  not after. The size check caught it either way, but only once the bytes
  were in memory — which is the entire cost a zip bomb exists to impose.
  `inflateRawSync` now carries `maxOutputLength`.

- **`materialise` / `fetchBody` no longer fire for non-catalog hits.** The
  engine calls them for any hit without a skill directory, which includes
  every hit a host-supplied source contributes; those arrived with an
  undefined id and fetched `/skills/undefined`, so an extra source cost a
  404 per hit.

- **`timeout_s` in `$HERMES_HOME/skillsearch.json` is read.** The plugin
  offered it in its setup and wrote it to the file, and
  `SkillSearchEngine.from_hermes` built the adapter without it — so every
  value a user chose was discarded for the 8-second default. Found while
  driving the plugin against an 891-skill corpus, where 8 seconds is not
  enough and the setting that exists to say so did nothing.

- **A query containing `$&`, `` $` `` or `$'` corrupted the rewrite prompt
  (TypeScript).** The user's text went into `String.replace` as a *string*
  replacement, where `$`-sequences are pattern references — quoting shell's
  `$'...'` syntax spliced pieces of the prompt into itself. The replacement
  is a function now, and the parity suite pins a `$`-laden query.
- **The two implementations indexed different text, so the same directory
  could rank differently.** Python indexed the name twice and capped the
  body at 4000 characters; TypeScript indexed the name once and the whole
  body. TypeScript now formats index text byte-identically
  (`formatSkillText`), pinned by the parity suite.
- **A frontmatter-less skill was named by the wrong directory
  (TypeScript).** The first path segment under the root, rather than the
  skill's own directory — so nested nameless skills collapsed into one
  entry under their grouping directory, and the split broke on Windows
  separators. Now `basename(dir)`, matching Python.
- **A timed-out model call was abandoned, not aborted (TypeScript).** The
  engine stopped waiting but the stream kept running, spending tokens on a
  reply nobody reads — and a late transport rejection surfaced as an
  unhandled promise rejection. `bounded()` now hands the call a signal that
  fires on timeout and on the turn's own cancellation.

- **The rewrite call was effectively unbounded on the hot path.** Its only
  ceiling was an internal 120s, and it runs before the gate on every turn,
  so a stalled provider held the whole turn for two minutes. Now bounded by
  `rewrite_timeout_s`; a timeout searches the raw query. The internal
  ceilings in the gate and the rewriter are gone — the engine owns both
  deadlines, and the gate's 180s constant had never been reachable at all.
- **A failed bundle extraction poisoned the cache permanently.** The cache
  hit was "the directory exists", and a rejected archive left a
  half-extracted directory behind, so every later install read it as a hit
  and fed the agent a truncated skill. Extraction now stages and renames.
- **Fusion picked collision representatives by raw score.** Scores are
  per-source scales that do not compare — BM25 unbounded, catalog quality
  in 0..1 — so the local copy won every collision regardless of how each
  source ranked it. The representative is now chosen by weighted rank, the
  same currency the fusion ranks in. *This changes which body is injected
  when two sources carry the same skill.*
- **Root layering did nothing.** The comment claimed later roots win, the
  code was first-wins, and the collapse key included the source name, so
  two roots could never collide at all: a user's `pdf-forms` and a bundled
  `pdf-forms` both entered the index and competed for rank. Earlier roots
  now shadow later ones, as the ordering always intended.
- **The published `MemoryRecall` protocol did not match the call.** It
  declared `recall(*, agent_id, query, limit)`; the source calls
  `recall(query, agent_id=..., top_k=...)` and also read a `score`
  attribute the protocol never mentioned. A third-party backend written
  from the docs raised `TypeError`, which the router swallowed as an empty
  result — a silent loss of every memory hit. The protocol now states the
  real signature, `score` is optional, and a regression test implements the
  protocol exactly as published.
- **The two model calls disagreed about parsing the same model.** The gate
  tolerated a fenced block anywhere, a reasoning preamble and a bare object
  in prose; the rewriter only handled a fence that ended the reply. Found
  against a live model: it answered `need_retrieval: false` and then added
  a sentence of explanation, so the rewriter failed to parse its own
  verdict and searched anyway. One extractor now serves both.
- **A timed-out adapter call left its coroutine running.** The Hermes and
  HTTP adapters stopped waiting but never cancelled, so against a slow
  backend one abandoned retrieval per turn accumulated on their private
  loop.

### Removed

- **The EverOS source, and the engine's knowledge of any one host's
  memory.** `SkillSearch(memory=...)`, `SearchConfig.agent_id` and
  `SearchConfig.weight_memory` are gone, along with
  `skillsearch/sources/everos_source.py` and the `MemoryRecall` port.
  **Breaking** for anyone passing them.

  In their place, `SkillSearch(extra_sources=[...])` — the Python twin of
  the TypeScript engine's `EngineParts.sources`. Anything shaped like a
  `SkillSource` is fused beside the two built-in ones, so a host with
  recall over self-evolved skills writes that adapter itself and the engine
  never learns what it is. Ten lines on the host's side; one fewer
  deployment's concepts in this package.

- **`plugin-raven/host-patches/`.** The `context_segments` slot it opened
  is going upstream instead. A patch is a fork to maintain, and this one
  had drifted from Raven's `main` and wired one of the three `AgentLoop`
  construction sites. Until the slot lands the plugin installs and never
  gets a stage to claim.

- **`skillsearch/adapters/hermes.py`** moved to `plugin-hermes/engine_adapter.py`,
  beside the plugin that is its only caller. **Breaking** for a direct
  importer; `engine-python/adapters/` now holds only `http_server.py`, the
  any-host-over-HTTP channel.

### Changed

- **The rewriter can no longer veto retrieval.** `need_retrieval` is out of
  the prompt, out of `RewriteResult`, and no longer consulted; a rewrite
  now only produces a cleaner query, and every failure path means "search
  the raw words". Measured here at a coin flip — one query, six live runs,
  `true` and `false` three each — and reached without sight of a single
  candidate. The gate sees the shortlist *and* the agent's tool list, so it
  is the only step with standing to decide that nothing should be injected.
  **Behaviour change**: turns the rewriter used to skip now fan out.

- **`{baseDir}` resolves before the gate, for skills already on disk.** The
  gate's prompt tells the model to reject a skill whose referenced files it
  cannot see, naming `{baseDir}` as the sign of one — and resolution used
  to run *after* the gate, so every local skill that shipped its own
  scripts arrived looking exactly like the thing the gate was told to
  reject. Remote bundles still materialise after the gate: that is a
  download, and only survivors should cost one.

- **The BM25 index text is name + description, not name + description +
  body.** The description is the retrieval contract of the `SKILL.md`
  format — authors are asked to write the trigger conditions there — and it
  is also what the gate reads, so indexing it alone keeps ranking and
  gating looking at the same text; a body is prose, and the largest single
  source of a spurious match. **Behaviour change**, with a real cost: a
  tool named only inside a body is no longer findable by name, and a skill
  with no description has only its directory name left to match on. Set
  `index_body=True` / `indexBody: true` to restore the old text.

- **Query terms the corpus cannot distinguish on are pruned.** A term in
  over half the documents — "skill", "run", "use", the vocabulary of the
  format itself — is dropped from the query rather than left to carry it,
  since BM25's idf keeps such a term just above zero rather than at it. An
  unrelated query now returns nothing from a local directory instead of
  returning whatever ranked first. Off below ten documents, where over half
  is two of three. Threshold and guard are shared by both implementations
  and pinned in the parity suite.

- **`gate` defaults to "on when a catalog is configured" rather than
  always on**, and TypeScript gained the independent `gate` / `rewrite`
  switches Python already had (configuring a `model` used to turn both on
  together). The gate is a precision instrument told to reject when unsure:
  a directory the user curates is better served by ranking plus `top_k`,
  now that an unrelated query returns empty on its own, while a catalog of
  unvetted skills needs the environment check that is the only thing
  catching a skill this agent has no tools for. An explicit value always
  wins. **Behaviour change** for a local-only deployment with a model
  configured: no gate call, and no gate latency.

- **Gate-failure fallback is `max_select` (2), not `top_k` (5).** Carried
  over from an earlier host integration where the count was 5 and a comment
  required it not change. Stated here because it is a silent behaviour
  difference for anyone porting from that integration: when the gate fails,
  2 skills are injected rather than 5.
- `RAVEN_GATE_LOG_PATH` is now `SKILLSEARCH_GATE_LOG_PATH`.
- `httpx` moved to the `hub` extra. The local pipeline — scan, BM25,
  fusion, gate, render — needs no third-party package, which the CI's
  bare-install job now enforces.
- `SkillsSegment(heading=...)` (raven adapter) is gone; the heading is
  rendered from `SearchConfig.heading` and the parameter was never read.
- The HTTP adapter no longer documents a `session_id` field. Retrieval is
  stateless and it was never read.

### Documentation

- **The repository presents as "SkillCorpus Plugins"** — the official
  agent-host plugins for SkillCorpus. Code is untouched: the engine, the
  packages and every import keep the `skillsearch` name.
- **The root READMEs are rebuilt around the reader**: install (paste one
  prompt to your agent, or by hand per host) and a 30-second local demo come
  first; new sections give an honest per-turn cost table, a "what leaves
  your machine" privacy disclosure, a guide to writing findable skill
  descriptions, troubleshooting (including `SKILLSEARCH_GATE_LOG_PATH`), and
  uninstall. Architecture, parity and contributor material moved to the
  back.
- Removed internal ticket numbers, retired source names, and host-internal
  class references from module and class docstrings.
- `LICENSE` carries the full Apache-2.0 text rather than the short notice.
- READMEs rewritten for release: the repository README now positions the
  package, points the `hub` source at
  [SkillHub](https://evermind.ai/skillhub) — the hosted endpoint over
  [SkillCorpus](https://github.com/EverMind-AI/SkillCorpus) — and carries
  the corpus paper's citation; per-implementation READMEs document the new
  configuration keys, and the cross-implementation equality claim now
  points at the parity suite that enforces it.
