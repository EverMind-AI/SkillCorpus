# Changelog

## Unreleased

### Added

- **A TypeScript implementation** under [`typescript/`](typescript), for
  DeepSeek Harness. Both implementations now sit side by side and the
  Python one moved into [`python/`](python); `pip install -e .` becomes
  `pip install -e python`.
- **`rewrite_timeout_s`** (Python, default 5s) and **`rewriteTimeoutMs`**
  (TypeScript, default 5s) bound the rewrite call.
- **`hub_download_timeout_s`** (default 30s) bounds a bundle download
  separately from a catalog query.
- **`SkillSearch.invalidate()`**, **`SkillSearch.set_provider()`** and
  **`SkillSearch.has_sources`** — the file-watcher, `/model`-switch and
  is-anything-configured paths a running host needs.
- **`hub_client=`** on `SkillSearch`, so a host can hand over the catalog
  client it already built rather than have a second one created.

### Fixed

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

### Changed

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

### Known, measured, not fixed

- The rewriter's `need_retrieval` verdict costs recall. Over six live runs
  one query flipped between `true` and `false` about half the time, and a
  `false` drops a skill both ranking and the gate would have selected. The
  prompt already says "when in doubt, choose retrieval". Recorded in both
  READMEs rather than worked around, because changing the prompt would
  break the byte-identical parity the two implementations are checked on.

### Documentation

- Removed internal ticket numbers, retired source names, and host-internal
  class references from module and class docstrings.
- `LICENSE` carries the full Apache-2.0 text rather than the short notice.
