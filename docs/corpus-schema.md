# SkillCorpus — corpus schema

**Status: accepted (2026-08-05).** This document is the contract for
`export/corpus.py`: `cli build`'s final step (`export.corpus`) writes exactly
what is described here. The sign-off record is in [Decisions](#decisions-signed-off).

## Design goals

- **One published open dataset.** One row = one skill (one `SKILL.md`).
- **Decoupled from any consumer.** No sync sentinels (`.stale`,
  `.refresh_endpoint`), no incremental-`sync()` artifacts, no consumer-specific
  columns. The corpus is a self-contained snapshot, not a live feed.
- **Safe by default.** Only rows with `deleted = 0 AND active = 1` are exported.
  `active = 1` is the GREEN-license gate, so **every published row is
  permissively licensed**, and merged/near-duplicate losers (soft-deleted) are
  excluded.

## Published layout

```
corpus/
├── skills.parquet              # the table below, one row per skill
├── attachments.tar.zst         # zstd tarball; each skill under an <skill_id>/ member
│   #   <skill_id>/scripts/…   <skill_id>/references/…   <skill_id>/… (assets, …)
└── README.md                   # dataset card (see below)
```

`attachments.tar.zst` is a zstd-compressed tarball; each skill's extra files go
under an `<skill_id>/` member prefix — mirroring the **entire skill directory
except `SKILL.md`** (`scripts/`, `references/`, and any other bundled files), at
their original relative paths. `SKILL.md` is deliberately **not** duplicated: its
body is the inline `body` column and its frontmatter is `frontmatter_raw`, so the
file is fully reconstructable.

## `skills.parquet` columns

Types are Arrow/Parquet logical types.

| # | column | type | null | description |
|---|--------|------|:----:|-------------|
| 1 | `skill_id` | string | no | Primary key. `{source}__{name_slug}__{hash8}`. |
| 2 | `name` | string | no | Skill name (from frontmatter). |
| 3 | `description` | string | no | Short description (from frontmatter). |
| 4 | `body` | string | no | `SKILL.md` markdown body, frontmatter stripped. Inline. |
| 5 | `frontmatter_raw` | string | yes | Original YAML frontmatter, JSON-encoded. Kept for reproducibility. |
| 6 | `source` | string | no | Provider key, e.g. `anthropics`, `awesome:owner/repo`. |
| 7 | `source_url` | string | yes | Upstream repository URL. |
| 8 | `source_path` | string | yes | Path of the skill within the origin repo (provenance). |
| 9 | `license` | string | no | SPDX id / license category. Always GREEN (permissive). |
| 10 | `category` | string | no | One of the 16 classes (see enum below). |
| 11 | `tags` | list&lt;string&gt; | no | Free-form tags; may be empty. |
| 12 | `quality_score` | float64 | no | Aggregate quality, 0.0–1.0. |
| 13 | `quality_subscores` | struct&lt;utility:int8, robustness:int8, safety:int8&gt; | yes | LLM judge sub-scores, 0–10 each. Null when never LLM-judged. |
| 14 | `safety_flags` | list&lt;string&gt; | no | Rule-based safety-scan flags. The current rule set is minimal (blocking rules reject at ingest, so they never reach the corpus) — **published rows are effectively always empty**; kept for forward-compatibility. |
| 15 | `content_hash` | string | no | SHA-256 of the normalized body (dedup / provenance). |
| 16 | `body_tokens` | int32 | no | Rough token estimate of the body. |
| 17 | `has_scripts` | bool | no | Whether the skill bundles a `scripts/` dir (under its member in the tarball). |
| 18 | `has_references` | bool | no | Whether the skill bundles a `references/` dir (under its member in the tarball). |
| 19 | `added_at` | timestamp[us, UTC] | no | First ingested into the corpus. |
| 20 | `updated_at` | timestamp[us, UTC] | no | Last updated in the corpus. |
| 21 | `attachment_path` | string | yes | The skill's `<skill_id>/` member prefix inside `attachments.tar.zst`; set iff it bundles any file besides `SKILL.md`. |

### `category` enum (16)

`DEV`, `FRONTEND-UI`, `DEVOPS-INFRA`, `TESTING`, `SECURITY`, `DATA`, `AI-ML`,
plus the remaining classes through `OTHER` (authoritative list:
`core.models.Category`).

### `safety_flags` values

Emitted by the rule-based scan (`curate.safety`). That scan was intentionally
narrowed to a single blocking rule (`blocked.malware`) that **rejects** a skill
at ingest rather than tagging it, so exported rows carry an empty list. The richer
safety signal lives in the LLM judge's flags, which are not part of the corpus
(see decision 3). Treat this column as a forward-compatible placeholder, and an
empty list as **not** a guarantee of safety.

## Fields intentionally NOT published

These exist in the producer DB but are internal and are dropped from the corpus:

| dropped field | why |
|---|---|
| `name_hash` | Internal near-duplicate key; `content_hash` already covers provenance. |
| `superseded_by` | Internal merge lineage; only set on soft-deleted losers, which are not exported. |
| `deleted`, `active` | Export filter conditions, not data (all rows are `deleted=0, active=1`). |
| raw `stored_path` | Producer-local library path; replaced by the portable `attachment_path`. |
| LLM judge `reason` / `flags` (inside `subscores`) | Verbose free text / redundant with `safety_flags`. See decision 3. |

## Dataset card (`README.md`) contents

- **Name / summary** of the dataset and what a "skill" is.
- **Provenance**: the source list and per-source row counts.
- **License**: all rows permissive (GREEN gate); per-license breakdown.
- **Column dictionary**: the table above.
- **Attachments layout**: how to resolve `attachment_path`.
- **Generation**: produced by `skillcorpus` via `cli build` (pipeline:
  discover → clone → curate → quality → dedup → license-audit → export).
- **Caveats**: `quality_score`/`quality_subscores` are LLM-judged (noisy);
  `safety_flags` are heuristic, not a security guarantee; near-duplicates across
  sources are merged (one winner kept).

## Decisions (signed off)

Signed off 2026-08-05.

1. **`body` — inline.** The body is the primary content and Parquet handles
   large strings well.
2. **`tags` / `safety_flags` — native `list<string>`** (not JSON strings):
   analytics-friendly and self-describing.
3. **`quality_subscores` — `struct` of the 3 numeric dims**
   (utility/robustness/safety); the LLM judge `reason` is dropped (verbose).
4. **Provenance columns `frontmatter_raw` / `source_path` / `content_hash` —
   kept** (all three): decoupling-safe and aid reproducibility.
5. **Timestamps — Parquet `timestamp[us, UTC]`** (the DB stores ISO strings;
   convert on export).
6. **Attachments — a single `attachments.tar.zst` (zstd tarball).** Each skill's
   files go under an `<skill_id>/` member; the whole skill directory minus
   `SKILL.md` is kept at original relative paths (not just `scripts/` /
   `references/`). `SKILL.md` is not duplicated (reconstructable from `body` +
   `frontmatter_raw`).
