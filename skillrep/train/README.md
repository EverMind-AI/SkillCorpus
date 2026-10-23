# Encoder fine-tuning

Code for fine-tuning Qwen3-Embedding-0.6B for skill retrieval with an in-batch InfoNCE loss and mined
hard negatives. The pipeline has four steps; each script documents its inputs, outputs and an example
command.

| Step | Script | What it does |
|---|---|---|
| 1 | `synthesize_requests.py` | An LLM writes one request per training skill, for which the skill is the right tool. |
| 2 | `mine_hard_negatives.py` | The untuned encoder retrieves the top 50 skills per request; seven pass the false-negative filters and become hard negatives, three random skills complete the ten. |
| 3 | `build_triplets.py` | Pairs each request with its skill as the positive and its mined skills as negatives. |
| 4 | `train_encoder.py` | In-batch InfoNCE fine-tuning. |

## Details

**Training skills.** Any pool in the format of `skillrep/data.py`. Leave out the benchmarks' gold
skills and their near-duplicates (`--exclude` in step 1).

**Requests (step 1).** An LLM writes one request per skill for a stratified sample of skills (each
category in proportion to its size), at temperature 0.8 jittered by +-0.1 per request; replies of 30
characters or fewer are dropped.

**Negatives (step 2).** Skills are encoded as `name | description[:500] | body[:8000]` and requests
as the training instruction plus the first 2,000 characters of the request, with the untuned
encoder (max length 4,096). The top-50 skills of a request are scanned in order and the first seven
that pass three filters become hard negatives: trigram Jaccard with the positive's body (first
3,000 characters) at most 0.6, cosine with the positive skill at most 0.92, and no shared
five-character name prefix. Three random skills from outside the top 50 are added.

**Triplets (step 3).** Each request is paired with its whole skill (name, description and body) and
with its mined skills, also whole.

**Training (step 4).** Default settings:

| Setting | Value |
|---|---|
| Base model | Qwen3-Embedding-0.6B, last-token pooling, L2 normalization |
| Loss | InfoNCE, temperature 0.05; each request against all positives and negatives in the batch, gathered across GPUs |
| Batch | 4 requests per GPU (32 per contrastive batch on 8 GPUs); 4 accumulation steps |
| Schedule | 1 epoch, AdamW, learning rate 2e-5, weight decay 0.01, cosine decay with 5% warmup, gradient clipping 1.0 |
| Lengths | max 1,024 tokens; requests truncated to 1,500 characters |
| Precision | bf16, gradient checkpointing, seed 42 |

Training requests use the instruction
`Instruct: Given a task description, retrieve the most relevant skill document that would help an agent complete the task\nQuery:`
directly followed by the request. At retrieval time, `skillrep/data.py` uses `QUERY_INSTRUCTION`.

## Commands

```bash
python train/synthesize_requests.py --pool data/train_pool.jsonl --out data/train_requests.jsonl \
    --api <chat completions URL> --model <model id> --exclude data/test_pids.json
python train/mine_hard_negatives.py --pool data/train_pool.jsonl --requests data/train_requests.jsonl \
    --model Qwen/Qwen3-Embedding-0.6B --out data/train_mined.jsonl
python train/build_triplets.py --mined data/train_mined.jsonl --pool data/train_pool.jsonl \
    --out data/train_triplets.jsonl
torchrun --nproc_per_node=8 train/train_encoder.py --train_data data/train_triplets.jsonl \
    --base_model Qwen/Qwen3-Embedding-0.6B --output_dir outputs/encoder
```

The trained encoder in `outputs/encoder/final` can be passed to `scripts/encode.py --model`.
