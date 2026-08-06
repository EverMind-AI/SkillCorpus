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
scripts/run_embedding.sh      # One-shot embedding training (4-GPU DDP, 2048, global batch 32)
scripts/run_reranker.sh       # One-shot reranker training (Steps 5-6, original recipe at 4096)
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
