#!/bin/bash
# Batch-run the raven agent over all SkillsBench tasks (with-skill mode:
# pre-retrieved skills injected, no live retrieval), with resume, per-task
# retry, and summary.json aggregation.
#
# Required env vars:
#   LLM_URL    - OpenAI-compatible endpoint base URL
#   LLM_MODEL  - model name served by that endpoint
# Optional:
#   LLM_KEY    - API key for the endpoint (default: none)
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PARALLEL="${1:-8}"
# Machine-specific locations are configured via env vars; paths default to
# directories next to this script where a sensible default exists.
PYTHON="${PYTHON:-python3}"
TASKS_DIR="${TASKS_DIR:-$SCRIPT_DIR/tasks}"

LLM_URL="${LLM_URL:?ERROR: set LLM_URL to an OpenAI-compatible endpoint base URL}"
LLM_MODEL="${LLM_MODEL:?ERROR: set LLM_MODEL to the model name served by LLM_URL}"
LLM_KEY="${LLM_KEY:-none}"
# Pre-retrieved skills: load from after_gate JSON instead of live retrieval.
SKILL_OUTPUTS_DIR="${SKILL_OUTPUTS_DIR:-$SCRIPT_DIR/skill_outputs}"
GATE_SUBDIR_MODEL="${GATE_SUBDIR_MODEL:-qwen3.5-397b}"   # matches after_gate_<name> dir (offline gate, independent of run model)
# SQLite DB that maps skill name -> source dir, so bundled attachments
# (scripts/*.py, etc.) get docker-cp'd into the container.
MASS_LIBRARY_DB="${MASS_LIBRARY_DB:-$SCRIPT_DIR/mass_library.db}"

for req in "$TASKS_DIR" "$SKILL_OUTPUTS_DIR"; do
    [ -d "$req" ] || { echo "ERROR: missing directory $req (set TASKS_DIR / SKILL_OUTPUTS_DIR)"; exit 1; }
done
[ -f "$MASS_LIBRARY_DB" ] || { echo "ERROR: missing $MASS_LIBRARY_DB (set MASS_LIBRARY_DB)"; exit 1; }

declare -A COMBOS_DEF
COMBOS_DEF[raven_skill]="run_raven.py|$LLM_MODEL|--api-base $LLM_URL --api-key $LLM_KEY --skill-outputs-dir $SKILL_OUTPUTS_DIR --gate-model $GATE_SUBDIR_MODEL --inject-max 5 --mass-library-db $MASS_LIBRARY_DB|PLACEHOLDER|raven-skill|raven"

COMBO_ORDER=(
    raven_skill
)

START_RUN="${2:-1}"
NUM_RUNS="${3:-1}"

run_one_task() {
    local task_dir="$1"
    local task=$(basename "$task_dir")

    local existing=$(find "$OUTPUT_DIR/$RESULT_PREFIX-$task" -name "result.json" 2>/dev/null | head -1)
    if [ -n "$existing" ]; then
        local reward=$(python3 -c "import json; print(json.load(open('$existing')).get('reward', 'N/A'))" 2>/dev/null || echo "?")
        if [ "$reward" = "None" ] || [ "$reward" = "N/A" ]; then
            echo "RETRY $task (previous reward=$reward, cleaning)"
            rm -rf "$OUTPUT_DIR/$RESULT_PREFIX-$task"
        else
            echo "SKIP $task (reward=$reward)"
            return 0
        fi
    elif [ -d "$OUTPUT_DIR/$RESULT_PREFIX-$task" ]; then
        echo "RETRY $task (no result.json, cleaning)"
        rm -rf "$OUTPUT_DIR/$RESULT_PREFIX-$task"
    fi

    local max_retries=2
    local attempt=0
    local success=false

    while [ $attempt -lt $max_retries ] && [ "$success" = "false" ]; do
        attempt=$((attempt + 1))
        [ $attempt -gt 1 ] && echo "RETRY $task (attempt $attempt/$max_retries)"

        rm -rf "$OUTPUT_DIR/$RESULT_PREFIX-$task"
        mkdir -p "$OUTPUT_DIR/$RESULT_PREFIX-$task"
        [ $attempt -eq 1 ] && echo "START $task"

        "$PYTHON" "$SCRIPT_DIR/$RUNNER" "$task_dir" \
            --model "$MODEL" \
            --max-turns 40 \
            --container-prefix "$CONTAINER_PREFIX" \
            $EXTRA_ARGS \
            -o "$OUTPUT_DIR" \
            > "$OUTPUT_DIR/$RESULT_PREFIX-$task/run.log" 2>&1
        local rc=$?

        local result_file=$(find "$OUTPUT_DIR/$RESULT_PREFIX-$task" -name "result.json" 2>/dev/null | sort | tail -1)
        if [ -n "$result_file" ]; then
            local reward=$(python3 -c "import json; print(json.load(open('$result_file')).get('reward', 'N/A'))" 2>/dev/null || echo "?")
            echo "DONE $task -> reward=$reward"
            success=true
        elif [ $rc -eq 0 ]; then
            echo "DONE $task -> reward=? (no result.json)"
        else
            echo "FAIL $task attempt $attempt (rc=$rc, see log)"
        fi

        docker rmi "$CONTAINER_PREFIX-img-$task" 2>/dev/null
    done

    if [ "$success" = "false" ]; then
        echo "GIVE UP $task after $max_retries attempts"
    fi

    docker builder prune -f > /dev/null 2>&1
}
export -f run_one_task

END_RUN=$((START_RUN + NUM_RUNS - 1))

echo "=========================================="
echo "  raven benchmark ($LLM_MODEL, with-skill only)"
echo "  ${#COMBO_ORDER[@]} combos x runs $START_RUN-$END_RUN (parallel=$PARALLEL)"
echo "  Skills (pre-retrieved): $SKILL_OUTPUTS_DIR (after_gate_$GATE_SUBDIR_MODEL)"
echo "  Started: $(date)"
echo "=========================================="

for run in $(seq $START_RUN $END_RUN); do
    for combo in "${COMBO_ORDER[@]}"; do
        DEF="${COMBOS_DEF[$combo]}"
        IFS='|' read -r RUNNER MODEL EXTRA_ARGS _PLACEHOLDER CONTAINER_PREFIX RESULT_PREFIX <<< "$DEF"
        OUTPUT_DIR="$SCRIPT_DIR/jobs_${combo}_run${run}"
        mkdir -p "$OUTPUT_DIR"
        LOG_FILE="$OUTPUT_DIR/batch_$(date +%Y%m%d_%H%M%S).log"

        export SCRIPT_DIR MODEL OUTPUT_DIR RUNNER EXTRA_ARGS CONTAINER_PREFIX RESULT_PREFIX
        export PYTHON

        echo ""
        echo "=========================================="
        echo "  Run $run | $combo"
        echo "  Runner: $RUNNER | Model: $MODEL"
        echo "  Output: $OUTPUT_DIR"
        echo "==========================================" | tee "$LOG_FILE"

        find "$TASKS_DIR" -mindepth 1 -maxdepth 1 -type d | sort | \
            xargs -P "$PARALLEL" -I{} bash -c 'run_one_task "$@"' _ {} 2>&1 | tee -a "$LOG_FILE"

        python3 -c "
import json, glob
results = []
for f in sorted(glob.glob('$OUTPUT_DIR/$RESULT_PREFIX-*/*/result.json')):
    results.append(json.load(open(f)))
rewards = [r['reward'] for r in results if r.get('reward') is not None]
total_tokens = sum(r.get('token_usage', {}).get('total_tokens', 0) for r in results)
summary = {
    'total': len(results),
    'avg_reward': sum(rewards)/len(rewards) if rewards else 0,
    'total_tokens': total_tokens,
    'results': results,
}
out = '$OUTPUT_DIR/summary.json'
json.dump(summary, open(out, 'w'), indent=2, default=str)
print(f'  => {len(rewards)} tasks, avg reward={summary[\"avg_reward\"]:.3f}, tokens={total_tokens:,}')
" 2>&1 | tee -a "$LOG_FILE"

        echo "  Finished $combo run $run: $(date)" | tee -a "$LOG_FILE"
    done
done

echo ""
echo "=========================================="
echo "  All runs done: $(date)"
echo "=========================================="
