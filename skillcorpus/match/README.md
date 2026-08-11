# skillcorpus / match

> Part of the **[SkillCorpus](../../README.md)** framework — the `match` stage (skill retrieval).

Given a task, retrieve the skills that help with it: a fine-tuned Qwen3-0.6B
bi-encoder for recall plus a Qwen3-0.6B reranker for precision. This package
holds their training recipe, the retrieval evaluation suite, and the serving
layer.

## Files

```
collect_skills.py             # Step 1: collect name/desc/body from the skill pool dir
generate_queries.py           # Step 2: synthesize queries per skill (needs an OpenAI-compatible API)
build_random_neg_data.py      # Step 3: build training data with random negatives
train_embedding.py            # Step 4: fine-tune the bi-encoder
build_reranker_data.py        # Step 5: retrieve top-20 to build listwise data
train_reranker.py             # Step 6: fine-tune the reranker
eval_compare.py               # Evaluation over the easy/hard/skillcorpus tiers
metrics.py                    # Retrieval metrics (nDCG/MRR/Hit/Recall)
serve.py                      # Serving: encoder + reranker behind an HTTP API
scripts/run_embedding.sh      # One-shot embedding training (Step 4)
scripts/run_reranker.sh       # One-shot reranker training (Steps 5-6)
scripts/run_server.sh         # One-shot server launch (serve.py)
```

## Environment

python 3.12; `pip install -r requirements.txt`. Serving both models takes
~2.5 GB VRAM.

## Reproduction

The skill pool, synthetic queries, and the eval_core benchmark are distributed
separately. Steps 1-3 regenerate them from your own skill pool; `eval_core/` is
required for evaluation.

```bash
# Steps 1-3: build training data (skip if you already have a triplets file)
python3 build_random_neg_data.py --queries data/queries.jsonl --skills data/skills.jsonl \
    --output data/train_triplets.jsonl --num_neg 3 --seed 42

# Step 4: train the bi-encoder
bash scripts/run_embedding.sh

# Evaluate (the first run encodes the three pools; cached reruns are ~1 min)
CUDA_VISIBLE_DEVICES=0 python3 eval_compare.py --phase embed --models Ours \
    --tiers easy hard skillcorpus --max_length 2048
```

Notes:
- Evaluation MUST pass `--max_length 2048`; the default 4096 mismatches the
  training length and degrades metrics
- Caches are fp32 — bf16 quantization error flips tasks on the ranking margin
- Results land in `outputs/eval/comparison_results_ml2048.json`, overwritten on
  each run

## Serving

`serve.py` loads both models once on a GPU and exposes them over HTTP:

```bash
bash scripts/run_server.sh                    # 127.0.0.1:9000, GPU 0
PORT=9002 CUDA_VISIBLE_DEVICES=4 bash scripts/run_server.sh
```

| Endpoint | Request | Response |
|---|---|---|
| `GET /health` | — | `{"ok": true}` |
| `POST /embed` | `{"texts": [...]}` | `{"embeddings": [[...], ...]}` |
| `POST /score` | `{"prompts": [...]}` | `{"scores": [0.0-1.0, ...]}` |

Embeddings are L2-normalized, so relevance is `dot(query_vec, doc_vec)`.
`/score` takes fully formatted prompts, wraps each in the yes/no judge template,
and returns `P("yes")` in input order. Errors come back as HTTP 500 with
`{"error": "..."}`, an oversized body as 413.

`POST /embed` speaks the producer's `skillrouter_remote` protocol, so to build
your **own** corpus against these models, run this server and point the
producer's embedding at it — near-duplicate detection then uses the fine-tuned
encoder:

```yaml
# configs/default.yaml (producer side)
embedding:
  provider: skillrouter_remote
  base_url: http://127.0.0.1:9000
```

`serve.py` exits unless `RERANKER_MODEL` and `EMBEDDING_MODEL` are set;
`run_server.sh` fills them in with the training outputs, so a plain launch works
once Steps 4 and 6 have run.

Notes:
- Encoding length must match training, same as in evaluation. `run_server.sh`
  sets `EMBED_MAX_LENGTH=2048`; `serve.py`'s own default is the generic 4096
- Requests are threaded but GPU forward passes are serialized behind a lock, so
  concurrent clients queue rather than fail. For more throughput, batch more per
  request or run one instance per GPU behind a load balancer
- No authentication and no rate limiting — hence the loopback default bind

Every path, model id and serving option is an environment variable with a
default; the scripts and `serve.py` declare them at the top of each file.
