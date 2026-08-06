#!/usr/bin/env bash
# Reranker training (Steps 5-6):
#   Step 5: retrieve top-20 with the trained embedding to build listwise data
#   Step 6: fine-tune Qwen3-Reranker-0.6B (listwise CE, 4-GPU DDP)
# NOTE: this release only retrained the embedding; the reranker has NOT been
#       retrained under the 2048 recipe. Parameters below are the original recipe (max_length=4096).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0,1,2,3}"

PY="${PY:-python3}"
TORCHRUN="$PY -m torch.distributed.run"

BASE_RERANKER="${BASE_RERANKER:-Qwen/Qwen3-Reranker-0.6B}"

# Prefer the embedding trained in this package (override with ENCODER=)
ENCODER="${ENCODER:-outputs/embedding/final}"

QUERIES="${QUERIES:-data/queries.jsonl}"
SKILLS="${SKILLS:-data/skills.jsonl}"
RERANKER_DATA=data/reranker_train.jsonl
RERANKER_OUT=outputs/reranker

mkdir -p data

echo "[Step 5] Building reranker training data (encoder: $ENCODER)..."
CUDA_VISIBLE_DEVICES=0 $PY build_reranker_data.py \
    --queries "$QUERIES" \
    --skills "$SKILLS" \
    --encoder_path "$ENCODER" \
    --output "$RERANKER_DATA" \
    --max_length 4096 \
    --batch_size 32 \
    --cache_dir outputs/emb_cache_reranker

echo "[Step 6] Training reranker (4-GPU)..."
$TORCHRUN --nproc_per_node=4 --master_port=29513 train_reranker.py \
    --train_data "$RERANKER_DATA" \
    --base_model "$BASE_RERANKER" \
    --output_dir "$RERANKER_OUT" \
    --max_length 4096 \
    --num_epochs 1 \
    --lr 1e-5 \
    --warmup_ratio 0.05 \
    --grad_accum_steps 16 \
    --save_steps 500 \
    --seed 42 \
    --bf16 \
    --gradient_checkpointing

echo "Reranker: $RERANKER_OUT/final"
