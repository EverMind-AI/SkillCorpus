#!/usr/bin/env bash
# Serve the trained embedding + reranker over HTTP (see serve.py).
#   RERANKER_MODEL=... EMBEDDING_MODEL=... bash scripts/run_server.sh
# Runs in the foreground; background it with nohup if you want a daemon.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"

PY="${PY:-python3}"

# Prefer the checkpoints trained in this package (override with the env vars)
export EMBEDDING_MODEL="${EMBEDDING_MODEL:-outputs/embedding/final}"
export RERANKER_MODEL="${RERANKER_MODEL:-outputs/reranker/final}"
export HOST="${HOST:-127.0.0.1}"
export PORT="${PORT:-9000}"

# Match the 2048 training recipe; serve.py's own default is the generic 4096.
export EMBED_MAX_LENGTH="${EMBED_MAX_LENGTH:-2048}"

exec "$PY" -u serve.py
