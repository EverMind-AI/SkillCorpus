# Skill Library

General-purpose skill library build pipeline — multi-source aggregation + CRUD + ingest filtering + proactive refresh.

**Architecture**: the producer (this repo) writes SQLite + faiss + a file tree, and exports
`mass_library.db` via `skill_library.export`; the consumer (`everclaw/skill_forge/`)
attaches that DB as a mass pool via `SqliteStore`, together with scripts/references attachments on the filesystem.
LLM as the primary path + rules as fallback; embedding goes through the shared SkillRouter remote endpoint (runtime retrieval is handled by the consumer).

---

## Quick Start

```bash
pip install pyyaml numpy click faiss-cpu sqlite-vec openai   # dependencies
python3 -m skill_library.cli build                        # build from scratch (default demo, 4 sources) -> data/index.db
python3 -m skill_library.cli stats                        # view library stats
python3 -m skill_library.cli build --update               # afterwards: incremental update (runs only due sources per cadence)
```

- **Build and update are the same command** `cli build` (distinguished by `--update`); see [Usage](#usage) for details.
- The public default reads `sources.yaml` (demo, 4 permissive sources); `data/` artifacts are not published with the repo, and `build` builds a local library from these sources. The full 62 sources live in the private `sources.full.yaml` (`--full`).
- See `config.yaml` for the embedding / LLM endpoints; **automatic degradation when endpoints are unreachable** (classification -> `OTHER`, retrieval -> BM25-only), so the pipeline still runs end to end.

---

## Status (after the /new endpoint migration, 2026-05-30)

```
producer index.db
  total              157,802
  active=1 deleted=0   96,401   (GREEN license, exportable)
  active=0 deleted=0   47,181   (non-GREEN, kept in the library but not exported)
  deleted=1            14,220   (dedup soft-delete)

consumer mass pool (post-license-filter + post-align)
  mass_library.db      96,401 rows  ·  1.2 GB  (embedding-our-new, byte-identical vs producer)

Endpoint            http://<EMBEDDING_HOST>/new   (internal network, self-trained model)
Embedding formula   name | desc[:500] | strip(body)[:8000]   (producer and consumer are aligned)
```

Architecture finalized: SQLite mass pool + shared GPU embedding/reranker endpoint + cron proactive refresh.

---

## Dependencies

**SkillRouter remote endpoint** (shared GPU service):
| Path | Purpose | Used by |
|---|---|---|
| `POST /embed   {"texts":[...]}`  → `{"embeddings":[[1024]]}` | embedding | **producer** (dedup + export vectors) |
| `POST /score   {"prompts":[...]}` → `{"scores":[...]}`         | reranker (P(yes)) | consumer (retrieval re-ranking; not used by producer) |

`config.yaml`'s `embedding.provider = "skillrouter_remote"` points at the endpoint, with 5 retry/backoff
attempts added in the helper to guard against RST.

**LLM calls** (classification / quality judge / dedup judge): a remote OpenAI-compatible endpoint,
see `config.yaml`'s `llm.endpoints`.

**Degradation**:
- LLM unavailable: classification falls back to `OTHER` + tags still use rule-based extraction
- Embedding unavailable: skip embedding dedup + retrieval degrades to BM25-only

---

## Architecture

```
┌─ PRODUCER (this repo) ──────────────────────┐
│  data/index.db        SQLite metadata       │
│  data/skill_index.faiss HNSW (dedup speedup)│
│  data/skills/<source>/<name>/{scripts,refs} │
│       ↓ ingest pipeline (concurrency 8)     │
│  parse → safety → quality length gate →     │
│  sub-skill filter → 3-layer dedup →         │
│  classify → LLM quality → embed → store     │
└──────────┬──────────────────────────────────┘
           │ export_to_mass_library
           │   ┌──→ mass_library.db   (DB: body + emb + meta)
           │   └──→ skills/<src>/<n>/  (FS: scripts/refs only;
           │                            SKILL.md not needed,
           │                            body is in DB)
           │   write .stale + .refresh_endpoint
           ▼
┌─ CONSUMER MOUNT ─────────────────────────────┐
│  mass_library.db        attach via SqliteStore
│  skills/<src>/<name>/   real path for {baseDir}
│  .stale                 next start consumes & clears
│  .refresh_endpoint      sentinel for `skill refresh` CLI
└──────────┬──────────────────────────────────┘
           │ consumer SqliteStore.iter_index_rows() → Retrieval
           │ + LocalPool (BM25 over workspace + builtin + everos_light)
           │ → RRF fusion
           ▼
       everclaw/skill_forge/
       (dense mass pool + lexical local pool)
```

### Three-layer ingest dedup

1. **Exact**: `content_hash` — identical SHA-256 of the normalized body → DUPLICATE
2. **Same-source canonical name**: name_hash hit → overwrite the old record
3. **Cross-source near-duplicate**: name_hash conflict across sources OR cosine ≥ 0.90 → `LLMDupJudge` secondary confirmation; cos ≥ 0.995 auto-marked as duplicate (cache)

### Parent-child skills (consistent across all three ends)

- Parent skill = top-level `<source>/<name>/SKILL.md` (depth=3 in producer fs)
- A SKILL.md under a subfolder = an ordinary attached file of the parent (readable via read_file, not indexed as a separate skill)
- producer `_drop_subskill_paths` (`pipeline.py`) filters at write time + consumer `_iter_skill_dirs`
  (`registry.py`) filters at read time = two-layer protection

### `{baseDir}` resolution

On export, the producer fills the `mass_library.db.path` column only for skills whose body actually references fs attachments (~40%; determined by
`export._dir_referenced_assets` directory-grounding detection — reading the real files in the skill directory and comparing them against the body).
After the consumer's `_row_to_meta` reads path,
`load_skills_for_context` replaces `{baseDir}` in the body with `meta.path.parent` (=
the real fs directory), so what the agent receives is an accessible absolute path. A sqlite-only row with `path=NULL`
triggers the `sqlite://` guard to skip the replacement, and the body is emitted as-is.

---

## Classification (LLM classifier, 16 classes)

| Group | Categories |
|---|---|
| Software dev stack (5) | DEV, FRONTEND-UI, DEVOPS-INFRA, TESTING, SECURITY |
| Data/AI (2) | DATA, AI-ML |
| Auth (1) | AUTH |
| Content output (4) | DOC-PROC, WRITING, MULTIMEDIA, COMMS |
| Workflow/office (2) | WORKFLOW, PRODUCTIVITY |
| Meta-tooling (1) | META |
| Fallback (1) | OTHER |

Implementation: `metadata.py` has a built-in classification prompt (self-trained Qwen3.5-397B, 100% hit rate on a 1000-sample test, 0 OOV).
**Tag**: `metadata.py`'s rules extract 3-5 keywords (independent of the main classification).

---

## Source Inventory & Reproducibility Boundaries

### Where the sources are listed — the registry (public demo + private full set)

**All source entries converge in a single YAML registry**; both `fetch.py` (full crawl) and `scripts/refresh_loop.py` (scheduled refresh) read from it, routed by `type` via `fetch.py:discover_repos`. **Adding/removing a source only touches the YAML, not the code.**

| File | Content | Published |
|---|---|---|
| `sources.yaml` | **public default = demo** (4 permissive git_clone sources: anthropics/skills, vercel-labs/skills, addyosmani/agent-skills, K-Dense-AI/scientific-agent-skills) | ✅ committed to git, public |
| `sources.full.yaml` | **full production set, 62 sources / 6 types** | ❌ excluded by `.gitignore`, private |

- Public users: `python -m skill_library.fetch` (default demo) → run the full pipeline, build a local library
- Production side: `python -m skill_library.fetch --config skill_library/sources.full.yaml` → full set

**Partially open-source model**: the public repo = complete code + demo `sources.yaml`, but **does not ship** `data/` (index.db/skills/mass_library are all excluded by .gitignore) or `sources.full.yaml`. Users can run the full pipeline and obtain a self-built local library, but cannot obtain the full source inventory or the finished 96K corpus.

Type distribution of the full 62 sources (private sources.full.yaml):

| type | Count | Discovery method |
|---|---:|---|
| `readme_scrape` | 19 | clone awesome lists + scrape the external links in the README (dynamically expands into thousands of repos) |
| `git_clone` | 38 | clone directly by name (including custom labels such as antigravity→sickn33, majiayu000) |
| `index_api` | 2 | REST API pagination (skillsdirectory + skillsmp) |
| `json_catalog` | 1 | clone + parse a JSON catalog (skillmanager) |
| `sitemap_scrape` | 1 | scrape the skills.sh sitemap → owner/repo |
| `lobehub_json` | 1 | clone + convert via lobehub_to_skills.py |

Also: `data/source_manifest.csv` (5,375 rows) is an **output snapshot** (which sources this data actually produced → repo URL → skill count, exported from index.db, migrated along with data/). sources.yaml is the **input registry** (what to fetch); the manifest is the **result ledger**.

(Retired entry points: `--from-skillsbench`/`--from-datahub` and fetch.py's old hard-coded `README_SOURCES`/`DIRECT_REPOS` constants, plus the 5 `--from-X` flags — all consolidated into sources.yaml or removed.)

### Can the current data be fully reproduced from an empty data/? **No bit-for-bit reproduction**

Among the registry's 62 entries, `readme_scrape`/`index_api`/`sitemap_scrape` **dynamically expand** into ~5,375 sources / 96,401 skills. Even if the upstream lists and all repos were **completely frozen**, five mutually independent factors still prevent byte-for-byte reproduction:

| # | Factor | Explanation |
|---|---|---|
| A | **Upstream list updates** | awesome lists add/remove links, repos are deleted/renamed/made private (the largest source) |
| B | **In-repo content drift** | `git clone --depth 1` takes the default branch's **latest commit, without pinning a SHA** → the same repo's content may already have changed |
| C | **LLM non-determinism** | `config.yaml temperature=0.1` (non-zero) → category + quality_score may differ per run; the endpoint model may be swapped |
| D | **dedup winner flips** | `_pick_winner`: quality (LLM) → source → `added_at` (ingest time). Concurrency 8 + time tie-break → which of the near-duplicates survives can change |
| E | **embedding drift** | vectors drive the dedup cosine thresholds (0.90/0.995), so borderline pairs flip; switching endpoints also changes the vectors |

(There are also operational transients: 404s/rate-limiting from the GitHub license API, and clone timeouts causing a different successful subset each time.)

### Mitigations & practical conclusions

- LLM scoring/dedup judgments are **cached by content_hash** (the `quality_judgments` / `dedup_judgments` tables). **Rerunning from a frozen `data/skills/` + the cache tables** → unchanged content hits the cache → C/D are largely reproducible; **re-fetching and re-judging from an empty data/** → no cache + a fresh clone → A–E all kick in → not reproducible.
- **Reproducibility depends on where you start**: the existing data snapshot (including the skills/ tree + index.db + caches) is ≈ reproducible and self-contained, ready to use directly; an empty-directory re-fetch ≠ reproducible (you get a similar but different library).
- Achieving high reproducibility from an empty directory would additionally require: pinning commit SHAs (fixes B) + temperature=0 + locking the model version (fixes C/E) + fixing a single-threaded ingest order (fixes D). **Not done currently** — the producer's goal is to "continuously absorb the latest skills", not to be a "replayable experiment". The current data is already a self-contained snapshot, and `source_manifest.csv` makes the sources auditable.

---

## File Structure

```
skill_library/
├── README.md / __init__.py / cli.py / config.yaml
│
│ ── ingest pipeline (by stage) ──
├── fetch.py            # source registry (load_registry/discover_repos, dispatch by type)
│                       #   + Stage 0: multi-source git clone (GIT_TERMINAL_PROMPT=0)
├── pipeline.py         # Coordinator (serial/concurrent + sub-skill filtering) + SkillLibrary top-level API
├── rules.py            # pure-rule stage: SKILL.md parsing + safety regex + license GREEN gate
├── dedup.py            # rule dedup (content/name hash + cosine) + LLM dedup judgment
├── metadata.py         # LLM stage: quality judge (3-facet/19-flag) + 16-class classification + tag
├── embed.py            # SkillRouter remote embedding client
├── store.py            # SQLite + sqlite-vec + faiss (includes SkillRecord schema)
├── export.py           # producer index.db → mass_library.db (full export + incremental sync)
│
│ ── helper tools ──
├── llm.py              # OpenAI-compatible LLM client (single endpoint)
├── license_audit.py    # license maintenance CLI: refresh/build/validate/apply/stats
│
│ ── config (committed to git) ──
├── sources.yaml                  # public demo source list (4 permissive sources)
├── sources.full.yaml             # full production set, 62 sources (git-ignored, private)
├── license_safe_sources.json     # GREEN-license allowlist
│
│ ── data (git-ignored) ──
├── data/
│   ├── index.db
│   ├── skill_index.faiss + skill_index_ids.json
│   └── skills/<source>/<name>/
│
│ ── ops scripts (non-pipeline) ──
├── scripts/
│   ├── rescan_dedup.py        # whole-library near-duplicate backfill
│   ├── rescan_quality.py      # whole-library LLM scoring backfill
│   ├── source_resync.py       # single-source incremental refresh (skips those already in the library)
│   ├── refresh_loop.py        # cron scheduling (runs due sources per cadence)
│   ├── refresh_server.py      # HTTP trigger (:8765, triggered remotely by the consumer)
│   └── lobehub_to_skills.py   # LobeHub agent JSON → SKILL.md
│
└── tests/ (9 unit tests)
```

---

## Usage

### One-command full pipeline — `cli build` (create + update share one entry point)

**Creation and incremental update are the same feature**, distinguished by `--update`. An empty library auto-inits (`open()` has a built-in
`init_schema`); the chain = discover → clone → ingest → quality → export:

```bash
python3 -m skill_library.cli build              # create from scratch (default demo, 4 sources, runs all)
python3 -m skill_library.cli build --update     # incremental update (runs only due sources per cadence)
python3 -m skill_library.cli build --full       # full registry (62 sources, production; can combine with --update)
python3 -m skill_library.cli build --source anthropics/skills   # run a single source only
python3 -m skill_library.cli build --dry-run    # discover + print only, no actual run
```

Under the hood it is `scripts/refresh_loop.py:run_refresh()` (`--update` flips `force`, `--full` switches the registry);
you can also run `python3 -m skill_library.scripts.refresh_loop [--config ...] [--force] [--source ...]` directly.

**Manual step-by-step** — `cli build` already chains the steps below automatically; use these only when you want to control each step individually:

```bash
# 1. Multi-source git clone → staged into experiment-results/_reference_skills/_fetched/<owner>/<repo>/
#    (default demo, 4 sources; for the full set add --config sources.full.yaml, ~30min/~6K repos)
python3 -m skill_library.fetch --workers 16

# 2. ingest into the DB (runs the full pipeline: parse→safety→quality→dedup→classify→embed→store)
#    input = the _fetched directory cloned in the previous step (store then writes ingested skills into data/skills/)
python3 -m skill_library.cli add-batch experiment-results/_reference_skills/_fetched/<owner>/<repo> --source <owner>/<repo>

# 3. (optional) whole-library backfill (a follow-up run after large-batch ingest was done with inline LLM disabled)
python3 -m skill_library.scripts.rescan_dedup
python3 -m skill_library.scripts.rescan_quality --workers 16

# 4. export to the consumer mass pool (writes mass_library.db + .stale)
python3 -m skill_library.export
```

### CRUD

```bash
python3 -m skill_library.cli stats
python3 -m skill_library.cli list --source anthropics/skills
python3 -m skill_library.cli get <skill_id>
python3 -m skill_library.cli retag <skill_id> "pdf,reportlab,financial"
python3 -m skill_library.cli reclassify <skill_id> DOC-PROC
python3 -m skill_library.cli delete <skill_id> [--hard]
python3 -m skill_library.cli add /path/to/skill-dir --source custom
```

### Python API

```python
from skill_library import SkillLibrary

with SkillLibrary().open() as lib:    # default path skill_library/data/
    lib.add("/path/to/skill", source="anthropics")
    lib.add_batch("/path/to/skills/", source="anthropics")
    print(lib.stats())
```

### Export to consumer (mass pool)

```bash
# Same machine, zero-config (assets-dir defaults to the producer data directory):
python3 -m skill_library.export \
    --dst <PATH_TO>/mass_library.db

# Cross-machine deployment: rsync the producer skills/ tree to the consumer side, then export pointing at the consumer path:
python3 -m skill_library.export \
    --dst /path/to/mass_library.db \
    --assets-dir /path/to/consumer/side

# Write the .refresh_endpoint sentinel (the consumer `skill refresh` auto-discovers it with zero config):
python3 -m skill_library.export \
    --refresh-endpoint http://producer-host:8765
```

Output:
- `mass_library.db` — all fields: body / embedding / frontmatter_json / path / is_always / requires_json
- `.stale` — consumed and deleted the next time the consumer attaches, used as a "new version available" signal
- `.refresh_endpoint` (optional) — auto-discovered by the consumer `skill refresh` CLI

On the consumer side, add the following to `~/.everclaw/config.json` (all of these are defaults in the new code, listed
explicitly only for reference):
```json
{
  "skill_forge": {
    "enabled": true,
    "mass_library_db": "<PATH_TO>/mass_library.db",
    "embedding_model": "embedding-our-new",
    "embedding_url": "http://<EMBEDDING_HOST>/new",
    "reranker_url":  "http://<EMBEDDING_HOST>/new",
    "embedding_api_key": "<EMBEDDING_API_KEY>",
    "reranker_api_key":  "<EMBEDDING_API_KEY>",
    "disable_always":    true,
    "injection_mode":    "full_body"
  }
}
```

**Key default value notes** (updated 2026-05-20):

- `disable_always`: **defaults to `true`** (flipped on 2026-05-20, previously `false`)
  - `true` (default): `always: true` skills go into neither the always block nor top-K, preventing
    double injection of mirror-side persona/mood skills + occupying top-K slots
  - `false`: always-true skills such as builtin (memory / self-improving) are auto-injected
    (compatible with the old behavior, but top-K gets polluted)

- `injection_mode`: defaults to `"full_body"` (eval measured top-1 keyword recall ~0.80)
  - `"summary"` is the alternative option (XML table of contents + the agent's own read_file), more token-efficient but
    recall drops to ~0.62; when choosing `summary` the agent must be familiar with the read_file flow

- `enabled`: defaults to `false` (master switch), must be explicitly `true` to enable the skill_forge feature.

- `embedding_model`: must align with the embedding model of `mass_library_db`
  (`embedding-our-new` ↔ `mass_library.db`); a mismatch breaks recall.

### Proactive refresh

```bash
# Start the HTTP trigger (lets the consumer CLI trigger it remotely)
python3 -m skill_library.scripts.refresh_server --port 8765 &

# cron auto-scheduling (runs only due sources per each source's pull_cadence in sources.yaml)
0 3 * * *   python3 -m skill_library.scripts.refresh_loop

# Manually run a specific source
python3 -m skill_library.scripts.refresh_loop --source openclaw/skills --force
```

Running `refresh_loop` via cron will:
1. Read `sources.yaml` + `data/refresh_state.json` to determine cadence
2. For those due: git pull → fast-batch ingest → rescan_quality → export_to_mass_library
   (writes mass_library.db + .stale)

### Near-duplicate backfill

```bash
python3 -m skill_library.scripts.rescan_dedup --dry-run --report /tmp/rescan.json
python3 -m skill_library.scripts.rescan_dedup --report /tmp/rescan.json
# Options: --min-cos 0.92 / --top-k 10 / --max-pairs 100 / --limit 500
```

### Quality scoring (LLM judge + persist to DB)

```bash
# Run the LLM judge on unscored / newly-added active skills; results are written to the quality_judgments table + skills.quality_score
python3 -m skill_library.scripts.rescan_quality --workers 16 --report /tmp/q.json
```

### License maintenance (`license_audit.py`)

Single-point maintenance of the source → license mapping, one subcommand per step: refresh / build / validate / apply / stats.

Data flow: `GitHub API (spdx_id)` → `source_license_report.csv` → `license_safe_sources.json` → `index.db skills.license / active`

```bash
# 1. Incremental fill: query the GitHub license for sources that have active skills in the DB but no record in the CSV
GITHUB_TOKEN=ghp_xxx python3 -m skill_library.license_audit refresh
#    --source <one>   query only one   --refresh-all   re-query those already in the CSV   --dry-run   preview

# 2. Rebuild the allowlist: CSV → license_safe_sources.json (keep only GREEN-class sources)
python3 -m skill_library.license_audit build [--dry-run]

# 3. Consistency check: cross-check CSV ↔ JSON ↔ DB (run in CI / before release)
python3 -m skill_library.license_audit validate

# 4. Backfill the DB: write the source-level CSV category into skills.license (only overwrites junk, leaves already-declared values untouched)
python3 -m skill_library.license_audit apply [--dry-run]

# 5. View distribution: source-level (CSV) + skill-level (DB) license distribution + GREEN/RED/YELLOW tag
python3 -m skill_library.license_audit stats
```

Routine: after adding sources run `refresh && build && apply`; before release run `validate` as a gate.
`validate` currently reports ~742 JSON↔CSV inconsistencies (the source license is RED/NO_LICENSE
but the skill's fm.license self-reports GREEN, e.g. lobehub Proprietary); this is a known signal, not a bug
(a single file's self-reported license does not override the whole repo's proprietary declaration).

### Source incremental refresh (single source, skips those already in the library)

```bash
python3 -m skill_library.scripts.source_resync /path/to/source --source anthropics
```

### Export bundle

```bash
python3 -m skill_library.cli export --category DOC-PROC --out /tmp/doc.zip
python3 -m skill_library.cli export --source anthropics/skills --out /tmp/anth.zip
```

---

## Testing

9 test files (pure `__main__`, runnable standalone, no pytest dependency):

```bash
# Run from the parent directory of skill_library; use sys.path.append to avoid same-named modules inside the directory shadowing the stdlib
cd <dir containing skill_library/>
for t in skill_library/tests/test_*.py; do
  python3 -c "import sys; sys.path.append('.'); import runpy; runpy.run_path('$t', run_name='__main__')"
done
```

| Test | Coverage |
|---|---|
| `test_parse.py` | SKILL.md frontmatter parsing + validate |
| `test_safety.py` | safety regex + `is_blocked` |
| `test_license_filter.py` | GREEN/RED/YELLOW determination + `is_green_license` |
| `test_classify.py` | 16-class classifier hits per class + tag extraction |
| `test_dedup_round_a.py` | canonical_name / LLMDupJudge cache / cross-source merge |
| `test_quality_round_b.py` | LLMQualityJudge cache/clamp + compute_quality weights |
| `test_e2e.py` | CRUD + quality rejection + export_bundle + reindex |
| `test_upgrade_smoke.py` | full export path smoke (index.db → mass_library.db) |
| `test_producer_review_fixes.py` | regression: faiss alignment / short-batch rejection / stale-dim drop / active anti-clobber / incremental stats not double-counted |

---

## External integration (consumer = everclaw/skill_forge)

The producer emits `mass_library.db` + fs assets; the consumer attaches them with `SqliteStore` and then
runs dense retrieval.

Integration summary in three sentences:
- producer/consumer share the SkillRouter remote endpoint, so CPU-only nodes can run it too
- the consumer attaches SqliteStore via the `mass_library_db` config; the dense pool + local BM25
  pool are fused via RRF; only attachments such as scripts/references remain on the FS, the body is already in the DB
- the user's `everclaw skill refresh <src>` remotely triggers the producer's git pull + ingest + export,
  zero-config (the `.refresh_endpoint` sits right next to mass_library.db)

---

## Scope

**Out of scope**: skill evolution / runtime quality tracking (4 counters) — that is a runtime concern; this library only handles the one-time ingest-time filtering and scoring. Adding a source only touches `sources.yaml` (see [Source Inventory](#source-inventory--reproducibility-boundaries)), with no code changes needed.
