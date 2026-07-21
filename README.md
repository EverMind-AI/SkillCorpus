# SkillCorpus

General-purpose skill-library build pipeline: multi-source aggregation → ingest
filtering → CRUD → proactive refresh. SkillCorpus is the **producer**; it builds
a local SQLite + faiss + file tree and exports a `mass_library.db` that the
**consumer** (Raven's `skill_forge`) attaches as a dense mass pool.

> Naming note: the product/repo is **SkillCorpus**; the importable Python
> package is still `skill_library` (kept for compatibility), so CLI/`-m` commands
> and imports below use `skill_library`.

LLM is the primary classification path with rule-based fallback; embeddings go
through the shared SkillRouter remote endpoint. Runtime retrieval lives on the
consumer side, not here.

---

## Quick Start

```bash
pip install pyyaml numpy click faiss-cpu sqlite-vec openai   # dependencies
python3 -m skill_library.cli build            # build from scratch (demo, 4 sources) -> data/index.db
python3 -m skill_library.cli stats            # library stats
python3 -m skill_library.cli build --update   # incremental update (only sources due per cadence)
```

- **Build and update are one command** (`cli build`, distinguished by `--update`).
- The public default reads `sources.yaml` (demo: 4 permissive sources). The full
  62-source set lives in the private `sources.full.yaml` (`--full`); `data/`
  artifacts are git-ignored and not published.
- Endpoints are in `config.yaml`. **Endpoints degrade gracefully** when
  unreachable: classification → `OTHER`, retrieval → BM25-only, so the pipeline
  still runs end to end.

---

## Architecture

```
┌─ PRODUCER (this repo) ──────────────────────┐
│  data/index.db          SQLite metadata      │
│  data/skill_index.faiss HNSW (dedup speedup) │
│  data/skills/<source>/<name>/{scripts,refs}  │
│      ↓ ingest pipeline (concurrency 8)       │
│  parse → safety → quality gate → sub-skill   │
│  filter → 3-layer dedup → classify → LLM     │
│  quality → embed → store                     │
└──────────┬───────────────────────────────────┘
           │ export_to_mass_library
           │   ├─→ mass_library.db  (body + embedding + meta)
           │   └─→ skills/<src>/<n>/ (scripts/refs only; body is in the DB)
           │   + .stale + .refresh_endpoint sentinels
           ▼
┌─ CONSUMER (Raven skill_forge) ───────────────┐
│  attaches mass_library.db via SqliteStore     │
│  dense mass pool + local BM25 pool → RRF      │
└───────────────────────────────────────────────┘
```

### Three-layer ingest dedup
1. **Exact** — `content_hash` (SHA-256 of the normalized body) → DUPLICATE.
2. **Same-source canonical name** — `name_hash` hit → overwrite the old record.
3. **Cross-source near-duplicate** — name_hash conflict across sources OR
   cosine ≥ 0.90 → `LLMDupJudge` confirmation; cosine ≥ 0.995 auto-marks a
   duplicate (cached).

### `{baseDir}` resolution
On export, the producer fills `mass_library.db.path` only for skills whose body
actually references filesystem attachments (~40%, via
`export._dir_referenced_assets`). The consumer then replaces `{baseDir}` in the
body with the real directory, so the agent receives an accessible absolute path.
A sqlite-only row (`path=NULL`) skips the replacement.

---

## Classification (LLM classifier, 16 classes)

| Group | Categories |
|---|---|
| Software dev stack (5) | DEV, FRONTEND-UI, DEVOPS-INFRA, TESTING, SECURITY |
| Data / AI (2) | DATA, AI-ML |
| Auth (1) | AUTH |
| Content output (4) | DOC-PROC, WRITING, MULTIMEDIA, COMMS |
| Workflow / office (2) | WORKFLOW, PRODUCTIVITY |
| Meta-tooling (1) | META |
| Fallback (1) | OTHER |

The classification prompt is built into `metadata.py` (self-trained
Qwen3.5-397B). Tags (3-5 keywords) are extracted by rules, independent of the
main classification.

---

## Sources & reproducibility

All source entries live in a single YAML registry read by both `fetch.py` (full
crawl) and `scripts/refresh_loop.py` (scheduled refresh); adding/removing a
source only touches the YAML.

| File | Content | Published |
|---|---|---|
| `sources.yaml` | public demo — 4 permissive git_clone sources | ✅ committed |
| `sources.full.yaml` | full production set, 62 sources / 6 types | ❌ git-ignored, private |

**Partially open**: the public repo ships the complete code + demo `sources.yaml`
but **not** `data/` or `sources.full.yaml`. You can run the full pipeline and
build your own local library, but not obtain the full source inventory or the
finished corpus.

**Not bit-for-bit reproducible from an empty `data/`**: `readme_scrape` /
`index_api` / `sitemap_scrape` entries expand dynamically, and five independent
factors prevent byte-identical rebuilds — upstream list churn, in-repo drift
(`--depth 1`, no pinned SHA), LLM non-determinism (`temperature=0.1`), dedup
winner flips, and embedding drift. LLM/dedup judgments are cached by
`content_hash`, so **re-running from a frozen `data/skills/` + cache tables** is
largely reproducible; **re-fetching from empty** is not. The producer's goal is
to continuously absorb the latest skills, not to be a replayable experiment;
`data/source_manifest.csv` keeps the sources auditable.

---

## File layout

```
skill_library/                     # importable package (product name: SkillCorpus)
├── cli.py / __init__.py / config.yaml
│  ── ingest pipeline ──
├── fetch.py       # source registry + multi-source git clone (Stage 0)
├── pipeline.py    # coordinator + SkillLibrary top-level API
├── rules.py       # SKILL.md parse + safety regex + license GREEN gate
├── dedup.py       # hash/cosine dedup + LLM dup judge
├── metadata.py    # LLM quality judge + 16-class classify + tag
├── embed.py       # SkillRouter remote embedding client
├── store.py       # SQLite + sqlite-vec + faiss (SkillRecord schema)
├── export.py      # index.db → mass_library.db (full + incremental)
│  ── helpers / config ──
├── llm.py / license_audit.py
├── sources.yaml / sources.full.yaml(private) / license_safe_sources.json
│  ── ops scripts ──
├── scripts/       # rescan_dedup, rescan_quality, source_resync,
│                  # refresh_loop (cron), refresh_server (:8765), lobehub_to_skills
└── tests/         # 9 unit tests
```

---

## Usage

### One-command pipeline — `cli build`
Create and incremental-update share one entry point (distinguished by
`--update`); an empty library auto-inits. Chain: discover → clone → ingest →
quality → export.

```bash
python3 -m skill_library.cli build                    # from scratch (demo, 4 sources)
python3 -m skill_library.cli build --update           # incremental (only due sources)
python3 -m skill_library.cli build --full             # full 62-source registry
python3 -m skill_library.cli build --source anthropics/skills   # single source
python3 -m skill_library.cli build --dry-run          # discover + print only
```

### CRUD
```bash
python3 -m skill_library.cli stats
python3 -m skill_library.cli list --source anthropics/skills
python3 -m skill_library.cli get <skill_id>
python3 -m skill_library.cli retag <skill_id> "pdf,reportlab,financial"
python3 -m skill_library.cli reclassify <skill_id> DOC-PROC
python3 -m skill_library.cli delete <skill_id> [--hard]
python3 -m skill_library.cli export --category DOC-PROC --out /tmp/doc.zip
```

### Python API
```python
from skill_library import SkillLibrary

with SkillLibrary().open() as lib:      # default path: data/
    lib.add("/path/to/skill", source="anthropics")
    lib.add_batch("/path/to/skills/", source="anthropics")
    print(lib.stats())
```

### Export to the consumer mass pool
```bash
# Same machine (assets-dir defaults to the producer data dir):
python3 -m skill_library.export --dst <PATH_TO>/mass_library.db

# Cross-machine: rsync the producer skills/ tree first, then point --assets-dir at it:
python3 -m skill_library.export --dst /path/to/mass_library.db --assets-dir /path/to/consumer

# Write the .refresh_endpoint sentinel (consumer `skill refresh` auto-discovers it):
python3 -m skill_library.export --refresh-endpoint http://producer-host:8765
```
Outputs: `mass_library.db` (body / embedding / frontmatter_json / path /
is_always / requires_json), `.stale` (consumed on the consumer's next attach),
and optional `.refresh_endpoint`.

### Proactive refresh
```bash
python3 -m skill_library.scripts.refresh_server --port 8765 &   # HTTP trigger
0 3 * * *  python3 -m skill_library.scripts.refresh_loop        # cron (only due sources)
python3 -m skill_library.scripts.refresh_loop --source openclaw/skills --force
```
`refresh_loop` reads cadence from `sources.yaml` + `data/refresh_state.json`, then
for due sources: git pull → fast-batch ingest → rescan_quality → export.

### Whole-library backfill / license maintenance
```bash
python3 -m skill_library.scripts.rescan_dedup   --report /tmp/rescan.json  # [--min-cos 0.92 --top-k 10 ...]
python3 -m skill_library.scripts.rescan_quality --workers 16 --report /tmp/q.json
python3 -m skill_library.license_audit refresh|build|validate|apply|stats  # source→license maintenance
```
License data flow: `GitHub API (spdx_id)` → `source_license_report.csv` →
`license_safe_sources.json` → `index.db skills.license / active`. Routine: after
adding sources run `refresh && build && apply`; run `validate` as a release gate.

---

## Testing

9 unit tests under `tests/` (also runnable standalone via `__main__`):

```bash
# skill_library must be importable (e.g. from the parent dir, or via PYTHONPATH)
python3 -m pytest tests/ --import-mode=importlib
```

| Test | Coverage |
|---|---|
| `test_parse.py` | SKILL.md frontmatter parse + validate |
| `test_safety.py` | safety regex + `is_blocked` |
| `test_license_filter.py` | GREEN/RED/YELLOW + `is_green_license` |
| `test_classify.py` | 16-class classifier + tag extraction |
| `test_dedup_round_a.py` | canonical_name / LLMDupJudge cache / cross-source merge |
| `test_quality_round_b.py` | LLMQualityJudge cache/clamp + compute_quality weights |
| `test_e2e.py` | CRUD + quality rejection + export_bundle + reindex |
| `test_upgrade_smoke.py` | full export path smoke (index.db → mass_library.db) |
| `test_producer_review_fixes.py` | faiss alignment / short-batch rejection / stale-dim drop regressions |

---

## Consumer integration (Raven skill_forge)

The producer emits `mass_library.db` + filesystem assets; the consumer attaches
them with `SqliteStore` and runs dense retrieval, fused with a local BM25 pool
via RRF. Producer and consumer share the SkillRouter remote endpoint, so
CPU-only nodes work too. The user's `skill refresh <src>` on the consumer
remotely triggers the producer's git pull → ingest → export (the
`.refresh_endpoint` sentinel sits next to `mass_library.db`, so it is
zero-config).

Consumer config keys (all default in current code): `mass_library_db`,
`embedding_model` (must match the DB's embedding model, e.g. `embedding-our-new`),
`embedding_url` / `reranker_url`, `injection_mode` (`full_body` default; `summary`
is more token-efficient but lower recall).

---

## Scope

**In scope**: one-time ingest-time filtering, dedup, classification, and scoring;
export to the consumer mass pool. **Out of scope**: skill evolution / runtime
quality tracking — those are consumer/runtime concerns. Adding a source only
touches `sources.yaml`, no code changes needed.
