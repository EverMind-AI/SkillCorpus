# Integrations

How to give an agent access to the SkillCorpus skills served by
[SkillHub](https://skillhub.evermind.ai).

## Three tiers

SkillHub exposes the corpus cheapest-first. Most skills are pure instructions, so tier 2
is where you usually stop; tier 3 is only for skills that ship scripts you intend to run.

```
① GET /openapi/v1/skills?q=…              discover — metadata, no body
② GET /openapi/v1/skills/{ref}            read     — skill_md + subscores + files
③ GET /openapi/v1/skills/{ref}/download   execute  — zip with scripts/assets
```

`{ref}` accepts either the UUID `id` or the raw `skill_id` string.

## Response envelope

Everything except `/health` and the zip download is enveloped. **`status == 0` means
success**; on failure `result` is `null`.

```json
{"error": "success", "requestId": "550e8400-…", "status": 0, "result": {}}
```

| `status` | HTTP | Meaning |
|---|---|---|
| `60001` | 404 | skill not found |
| `60002` | 400 | invalid parameter |
| `60003` | 503 | download failed |
| `60005` | 429 | rate limited (`Retry-After` header) |
| `20001` | 500 | internal error |

Rate limits are per IP: **120/min** for discover + read, **30/min** for download.

## 1. Discover

```
GET /openapi/v1/skills?q=<keywords>                                   # q required, no paging
GET /openapi/v1/skills/search?q=&tags=&category=&min_score=&page=&limit=
```

On `/search` every parameter is optional: `tags` is comma-separated and intersected,
`min_score` is a `quality_score` floor in 0–1, `limit` is 1–50.

```bash
curl "https://skillhub.evermind.ai/openapi/v1/skills/search?q=extract+tables+from+a+PDF&category=DOC-PROC&min_score=0.75&limit=2"
```

`result` is `{items, total}` (plus `page`, `limit` on `/search`). Items carry
`id`, `skill_id`, `name`, `description`, `source`, `category`, `quality_score`, `tags`,
`body_tokens`, `source_url`, `github_star`, `license`, `install_count`, `download_url` —
**no body**.

## 2. Read the body

```
GET /openapi/v1/skills/{ref}
```

Adds to the discover fields:

| Field | Meaning |
|---|---|
| `skill_md` | full `SKILL.md` body — **this is what you inject** |
| `files` | relative paths bundled in the package |
| `safety_flags` | audit labels, e.g. `["no_steps"]` |
| `subscores` | `{utility, robustness, safety, flags}`, each 0–10; may be `null` on older rows |
| `score_safety` / `score_robustness` / `score_availability` | the same facets normalised to 0–1 |
| `added_at` | ingest timestamp |

## 3. Download the bundle

```
GET /openapi/v1/skills/{ref}/download?source=<raven|everme|cli|web>
```

Returns **raw zip bytes**, not an envelope. `source` is optional; passing it records an
install event and increments `install_count`. Any other value is rejected with `60002`.

```bash
curl -o skill.zip "https://skillhub.evermind.ai/openapi/v1/skills/<id>/download?source=cli"
```

The archive wraps everything in a single `<skill-name>/` directory. **Validate paths on
extraction** — do not trust the relative paths inside the zip.

## Health

```
GET /health   →   {"status": "ok"}
```

---

## Raven

First-party source. Raven fuses SkillHub with its local and Everos skill sources through
weighted RRF; the block lives under `skillForge.router` (see `raven/config/raven.py`,
`HubSourceConfig`):

```yaml
skillForge:
  enabled: true
  router:
    enabled: true
    top_k: 5
    weights: { local: 1.0, everos: 0.9, hub: 0.85 }
    dedup_by: name
    over_fetch_factor: 2
    hub:
      endpoint: https://skillhub.evermind.ai
      api_key: null          # public skills need none
      timeout_s: 2.0
      min_safety: 0.7        # drops skills whose score_safety is lower
      source: raven
```

Raven reads bodies through tier 2 (`read_skill`) and only falls to tier 3 (`use_skill`)
for skills bundling executables.

## Any other harness

There is no first-party plugin for OpenClaw, Hermes, Claude Code or others yet. Two
generic paths work today:

**Skills-directory harnesses** — download the bundle and drop it in:

```bash
python examples/skillhub_demo.py --install ~/.claude/skills "convert a PDF to images"
#                                          ~/.hermes/skills      (Hermes)
#                                ~/.openclaw/workspace/skills    (OpenClaw)
```

**Prompt-injection harnesses** — skip the download entirely: fetch `skill_md` from tier 2
and prepend it to the system prompt. That is all `build_prompt()` in
[`examples/skillhub_demo.py`](../examples/skillhub_demo.py) does:

```python
blocks = "\n\n".join(f'<skill name="{s["name"]}">\n{s["skill_md"]}\n</skill>' for s in skills)
prompt = f"You have been given the following skills…\n\n{blocks}\n\nTask: {task}"
```

## Self-hosting the retrieval stack

If you would rather not depend on the hosted endpoint, the released corpus plus the
retrieval and reranker models are enough to run selection yourself: encode every skill
once, encode the task query, take the top-*k* by cosine, then rerank.

Both models are released:

| Role | Base | Objective |
|---|---|---|
| bi-encoder (candidate recall) | `Qwen3-Embedding-0.6B` | InfoNCE on synthetic queries |
| reranker (scoring) | `Qwen3-Reranker-0.6B` | listwise CE |

[`skillcorpus/match/serve.py`](../skillcorpus/match/serve.py) (launch:
`bash skillcorpus/match/scripts/run_server.sh`) stands both models up behind one endpoint,
exposing `POST /embed` and `POST /score` — see
[`skillcorpus/match/` → Serving](../skillcorpus/match/README.md#serving).

Note this is the **model** endpoint (`/embed` + `/score`), not the SkillHub
`/openapi/v1/skills` API — that service is hosted-only. A self-hosted stack therefore runs
its **own** selection over `/embed` + `/score`; `skillhub_demo.py` and Raven's
`skillForge.router.hub.endpoint` speak the SkillHub API and target the hosted endpoint. To
curate your own corpus against these models, point the producer's embedding at the endpoint
(`embedding.provider: skillrouter_remote`); see [`docs/running.md`](running.md).
