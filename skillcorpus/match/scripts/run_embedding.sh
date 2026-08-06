#!/usr/bin/env bash
# Embedding training: 3 random negatives + in-batch InfoNCE (temp=0.05), max_length=2048,
# 4-GPU DDP, bs2 x accum4 per GPU (global batch 32), seed 42, 1 epoch.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0,1,2,3}"

# Override via env vars: PY (python), BASE_EMB (base model path or HF id), TRIPLETS (training data)
PY="${PY:-python3}"
TORCHRUN="$PY -m torch.distributed.run"

BASE_EMB="${BASE_EMB:-Qwen/Qwen3-Embedding-0.6B}"
# Training data: build with build_random_neg_data.py (fixed seed -> reproducible md5)
TRIPLETS="${TRIPLETS:-data/train_triplets.jsonl}"
EMB_OUT=outputs/embedding

echo "=== Embedding training: 3 random negs, temp=0.05, max_length=2048, 4-GPU ==="
echo "Start: $(date)"
echo "Triplets: $(wc -l < $TRIPLETS)"
echo ""

$TORCHRUN --nproc_per_node=4 --master_port=29512 train_embedding.py \
    --train_data "$TRIPLETS" \
    --base_model "$BASE_EMB" \
    --output_dir "$EMB_OUT" \
    --max_length 2048 \
    --batch_size 2 \
    --grad_accum_steps 4 \
    --num_epochs 1 \
    --lr 2e-5 \
    --warmup_ratio 0.05 \
    --temperature 0.05 \
    --save_steps 500 \
    --seed 42 \
    --bf16 \
    --gradient_checkpointing

echo ""
echo "=== Training complete! ==="
echo "Embedding: $EMB_OUT/final"
echo "Finished: $(date)"
