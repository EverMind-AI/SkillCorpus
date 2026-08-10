# Integrations

How to give an existing agent harness access to SkillCorpus skills.

> **Status: placeholder.** The SkillHub endpoint and its client are not published yet.
> Everything below marked `TODO` needs the real values before release.

## The contract

Any harness that can inject text into a system prompt can use SkillCorpus. The loop is:

1. Send the task description to the SkillHub search endpoint.
2. Get back the top-*k* skills, each with a `body` (the full `SKILL.md` content).
3. Paste the bodies into the agent's prompt before it starts working.

```
task ──► POST /v1/skills/search ──► top-k skills ──► inject `body` into prompt ──► agent runs
```

<!-- TODO(@team): confirm request/response shape against the deployed service. -->

```bash
curl -X POST https://<SKILLHUB_URL>/v1/skills/search \
  -H 'Content-Type: application/json' \
  -d '{"query": "<task description>", "top_k": 3}'
```

| Field | Meaning |
|---|---|
| `query` | the task description, in natural language |
| `top_k` | how many skills to return |
| `body` | full `SKILL.md` content — this is what you inject |
| `license` / `source_url` | per-skill terms; downstream use must follow them |

## OpenClaw

<!-- TODO(@team): real config key + file path. -->

```yaml
# ~/.openclaw/config.yaml
skills:
  provider: skillhub
  endpoint: https://<SKILLHUB_URL>
  top_k: 3
```

## Raven

<!-- TODO(@team): real config key + file path. -->

```yaml
# raven config
skill_forge:
  provider: skillhub
  endpoint: https://<SKILLHUB_URL>
  top_k: 3
```

## Hermes

<!-- TODO(@team): Hermes integration — config shape unknown, fill in. -->

## Self-hosting the retrieval stack

If you would rather not depend on the hosted endpoint, the released embedding model plus the
corpus is enough to run retrieval yourself: encode every skill once, encode the task query,
take the top-*k* by cosine, then rerank.

<!-- TODO(@team): `skillcorpus/match/` currently ships training scripts only
     (collect_skills / generate_queries / train_embedding / train_reranker / eval_compare)
     — there is no inference entry point. Either add a small `match/serve.py`
     (encode → search → rerank) or point here at the SkillHub server code. -->

See [`skillcorpus/match/`](../skillcorpus/match) for the training recipe and
[`docs/running.md`](running.md) for endpoint configuration.
