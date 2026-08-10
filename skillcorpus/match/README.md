# SkillRouter Embedding

> Part of the **[SkillCorpus](../../README.md)** framework — the `match` stage (skill retrieval).

SkillRouter training recipe (3 random negatives, in-batch InfoNCE with temp=0.05,
global batch 32, seed 42, 1 epoch) with **max_length=2048**, fine-tuning
Qwen3-Embedding-0.6B as the bi-encoder.

Compared against the max_length=4096 variant (all other settings identical),
quality is on par — **with train/inference length aligned at 2048 there is no
quality loss, while training and encoding cost are roughly halved.**

## Files

```
collect_skills.py             # Step 1: collect name/desc/body from the skill pool dir
generate_queries.py           # Step 2: generate synthetic queries per skill via LLM (needs an OpenAI-compatible API)
build_random_neg_data.py      # Step 3: build training data with 3 random negatives from queries/skills
train_embedding.py            # Step 4: fine-tune bi-encoder with InfoNCE
build_reranker_data.py        # Step 5: retrieve top-20 with the trained encoder to build listwise data
train_reranker.py             # Step 6: fine-tune reranker with listwise CE
eval_compare.py               # Evaluation (easy/hard/skillcorpus tiers; --max_length controls encoding length)
metrics.py                    # Retrieval metrics (nDCG/MRR/Hit/Recall), used by eval_compare.py
serve.py                      # Serving: trained encoder + reranker behind an HTTP API
scripts/run_embedding.sh      # One-shot embedding training (4-GPU DDP, 2048, global batch 32)
scripts/run_reranker.sh       # One-shot reranker training (Steps 5-6, original recipe at 4096)
scripts/run_server.sh         # One-shot server launch (serve.py)
```

Note: this release only retrained the embedding model; the reranker script keeps
the original recipe parameters (4096). Train it yourself if needed.

## Data

The skill pool, synthetic queries, and the eval_core benchmark
(easy/hard/skillcorpus pools + 75 queries) are distributed separately.
Steps 1-3 can regenerate `data/` from your own skill pool; `eval_core/` is
required for evaluation.

## Data layout (defaults; every path is overridable via env vars)

```
data/skills.jsonl        # skill pool  (Step 1 output; also the skillcorpus eval pool)
data/queries.jsonl       # synthetic queries (Step 2 output)
data/train_triplets.jsonl# training data (Step 3 output; fixed seed -> reproducible md5)
eval_core/               # benchmark: easy/ hard/ pools (part-*.jsonl.gz), tasks.jsonl, relevance.json
outputs/embedding/final  # trained encoder (Step 4 output; MODEL_PATH overrides)
cache/                   # pool embedding cache, created by eval_compare.py (CACHE_DIR overrides)
```

Env overrides: `EVAL_CORE_DIR`, `DATA_DIR`, `CACHE_DIR`, `MODEL_PATH` for
eval_compare.py; `PY`, `BASE_EMB`, `TRIPLETS`, `CUDA_VISIBLE_DEVICES` for
run_embedding.sh; `PY`, `BASE_RERANKER`, `ENCODER`, `QUERIES`, `SKILLS` for
run_reranker.sh. Base models default to the Hugging Face ids
`Qwen/Qwen3-Embedding-0.6B` / `Qwen/Qwen3-Reranker-0.6B`; point `BASE_EMB` /
`BASE_RERANKER` at local checkpoints for offline use.

## Environment

python 3.12; `pip install -r requirements.txt` (torch >= 2.8,
transformers >= 5.2, numpy, pyyaml; openai only for generate_queries.py).
Override the interpreter with `PY=`.

## Reproduction

```bash
# 0) Build training data (or drop an existing triplets file at data/train_triplets.jsonl)
python3 build_random_neg_data.py --queries data/queries.jsonl --skills data/skills.jsonl \
    --output data/train_triplets.jsonl --num_neg 3 --seed 42

# 1) Training (4 GPUs, ~2.5-3.5 h)
bash scripts/run_embedding.sh

# 2) Evaluation (first run encodes the three pools online, ~2.5 h on one GPU;
#    cached reruns ~1 min)
CUDA_VISIBLE_DEVICES=0 python3 eval_compare.py --phase embed --models Ours \
    --tiers easy hard skillcorpus --max_length 2048
```

Notes:
- Evaluation MUST pass `--max_length 2048` (the default 4096 would mismatch the
  training length and degrade metrics)
- Pool embedding caches are saved in fp32 (bf16 quantization error flips tasks on
  the ranking margin)
- Results are written to `outputs/eval/comparison_results_ml2048.json`, overwritten
  on each run

## Serving

`serve.py` loads the trained encoder and reranker once on a GPU (~2.5 GB VRAM
for two 0.6B models in bfloat16) and exposes them over HTTP:

```bash
bash scripts/run_server.sh                    # 127.0.0.1:9000, GPU 0
PORT=9002 CUDA_VISIBLE_DEVICES=4 bash scripts/run_server.sh
```

`RERANKER_MODEL` and `EMBEDDING_MODEL` are required by `serve.py` and default to
`outputs/reranker/final` / `outputs/embedding/final` in the launch script; both
take a directory with `config.json`, tokenizer files and `model.safetensors`.
Other env overrides: `HOST`, `PORT`, `DEVICE`, `DTYPE`, `BATCH_SIZE`,
`MAX_LENGTH`, `EMBED_MAX_LENGTH`, `MAX_BODY_BYTES`.

| Endpoint | Request | Response |
|---|---|---|
| `GET /health` | — | `{"ok": true}` |
| `POST /embed` | `{"texts": [...]}` | `{"embeddings": [[...], ...]}` |
| `POST /score` | `{"prompts": [...]}` | `{"scores": [0.0-1.0, ...]}` |

```bash
curl -s localhost:9000/embed -H 'Content-Type: application/json' \
    -d '{"texts": ["resolve git merge conflicts"]}'
```

Embeddings are L2-normalized, so relevance is `dot(query_vec, doc_vec)`.
`/score` expects fully formatted prompts; the server wraps each in the yes/no
judge template and returns `P("yes")`, aligned with the input order. Errors come
back as HTTP 500 with `{"error": "..."}`, an oversized body as 413.

Notes:
- Encoding length must match training, as with `--max_length 2048` in
  evaluation. `run_server.sh` sets `EMBED_MAX_LENGTH=2048` for that reason;
  `serve.py`'s own default is the generic 4096
- Requests are threaded but GPU forward passes are serialized behind a lock (one
  GPU cannot run overlapping batches safely); concurrent clients queue rather
  than fail. For more throughput, batch more per request or run one instance per
  GPU behind a load balancer
- No authentication and no rate limiting — the bind address defaults to loopback
  for that reason. Put a reverse proxy in front if it must be reachable remotely
- Checkpoints saved by transformers 5.x keep `rope_theta` under a nested
  `rope_parameters` key; loading them on 4.x silently falls back to the Qwen3
  default (`10000` instead of `1000000`) and retrieval quality collapses. Stay on
  transformers >= 5.2 — `serve.py` warns at startup when it sees a low value
