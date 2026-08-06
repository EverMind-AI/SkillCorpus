# Running the pipeline

## Layout: what goes where

- **Repo inputs** (hand-written, code-reviewed): `configs/` (default.yaml,
  production.yaml, sources.demo.yaml) and `audit/` (the license whitelist).
- **Data** (everything the pipeline writes) lives under `SKILLCORPUS_HOME`,
  outside the repo — default `~/.skillcorpus`, override with the env var:

  ```
  $SKILLCORPUS_HOME/
  ├── cache/<owner>/<repo>/   # git clone cache (can be tens–hundreds of GB)
  ├── index.db               # SQLite + vec_skills + faiss sidecar
  ├── skills/<source>/<name>/# the attachment tree
  ├── state/                 # run state
  └── export/                # export staging
  ```

  ```bash
  export SKILLCORPUS_HOME=/data/skillcorpus   # e.g. a large data disk
  ```

## Configuring the endpoints

`configs/default.yaml` holds the LLM and embedding settings. Both clients are
**OpenAI-compatible**; the LLM uses `endpoints[0]` (listing more does not
load-balance). Point them at your own deployments:

```yaml
embedding:
  provider: "openai_compatible"
  model: "Qwen/Qwen3-Embedding-0.6B"
  dim: 1024
  base_url: "http://localhost:8100/v1"     # serves POST /v1/embeddings

llm:
  provider: "openai_compatible"
  endpoints:
    - { base_url: "http://localhost:8211/v1", model: "your-model", api_key: "dummy" }
```

**Graceful degradation** — if an endpoint is unreachable, classification falls
back to `OTHER` and the LLM quality/dup judges are skipped, so the pipeline still
runs end to end (with weaker metadata). This means a build never hangs on a bad
gateway; check `stats` to see whether the LLM path was active.

### Self-hosting the embedding model

Any server exposing `POST /v1/embeddings` works. To reproduce the paper's
retrieval, serve the released fine-tuned `Qwen3-Embedding-0.6B` (see the model
card) behind such an endpoint and set `embedding.base_url` / `dim` to match. The
retrieval / training recipe itself lives in `skillcorpus/match/`.

## Running a build

```bash
python -m skillcorpus.cli build                              # demo: configs/sources.demo.yaml (4 sources)
python -m skillcorpus.cli build --sources-config my.yaml     # your own registry
python -m skillcorpus.cli build --full                       # configs/sources.full.yaml (private, not shipped)
python -m skillcorpus.cli build --source anthropics/skills   # one source
python -m skillcorpus.cli stats                              # counts by source / category / license
```

`cli build` runs the whole chain: discover → clone → ingest (parse / safety /
classify / quality / store) → quality_pass → dedup_pass → license_audit →
export.corpus. The corpus lands in `$SKILLCORPUS_HOME/corpus/` (or the `--out`
of a standalone `export`).

## Reproducibility bounds

The corpus is **not bit-for-bit reproducible from an empty `SKILLCORPUS_HOME`**.
Five independent factors prevent byte-identical rebuilds:

1. **Upstream churn** — discovery sources (`readme_scrape` / `index_api` /
   `sitemap_scrape`) expand against live lists that change over time.
2. **In-repo drift** — clones are `--depth 1` with no pinned SHA, so a source's
   content moves between runs.
3. **LLM non-determinism** — classification / quality judging run at
   `temperature > 0`.
4. **Dedup winner flips** — near-duplicate arbitration can pick a different
   winner when embeddings shift.
5. **Embedding drift** — a different embedding model / version changes the
   vector space.

LLM and dedup judgements are cached by `content_hash`, so **re-running from a
frozen `$SKILLCORPUS_HOME/skills/` + the cache tables is largely reproducible**;
**re-fetching from empty is not**. The producer's goal is to keep absorbing the
latest skills, not to be a replayable experiment — `audit/source_manifest.csv`
keeps the source → skill-count → licence mapping auditable for a given release.
