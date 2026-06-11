#!/bin/bash
set -euo pipefail

ROOT="${SDMC_ROOT:-<SERVER1_SDMC_ROOT>}"
DATA_ROOT="${SDMC_DATA_ROOT:-<SERVER1_DATA_ROOT>/SDMC_remote_run/local_data}"
PYTHON_BIN="${SDMC_CLIENT_PYTHON:-/data/shared_envs/vllm-0.21-gemma4/bin/python}"
DATASET="${1:-spider}"
PORT="${2:-18114}"
LIMIT="${3:-1000000}"
RUN_ID="${4:-chess_${DATASET}_gemma4_p${PORT}_$(date +%Y%m%d_%H%M%S)}"
OUT_DIR="$ROOT/outputs/rq_final_20260608_023504/baseline_runs/$RUN_ID"
mkdir -p "$OUT_DIR"

ENDPOINT="http://127.0.0.1:${PORT}/v1"
if ! curl -fsS "$ENDPOINT/models" >/dev/null; then
  echo "Gemma4 endpoint $ENDPOINT is not reachable." >&2
  exit 3
fi

cd "$ROOT"
export OPENAI_API_KEY="${OPENAI_API_KEY:-local-calibration}"
export OPENAI_API_BASE="$ENDPOINT"
export OPENAI_BASE_URL="$ENDPOINT"
export CHESS_LOCAL_MODEL="${CHESS_LOCAL_MODEL:-gemma4_26b_sdmc}"
export CHESS_MAX_TOKENS="${CHESS_MAX_TOKENS:-512}"
export TOKENIZERS_PARALLELISM=false
export INDEX_SERVER_HOST="${INDEX_SERVER_HOST:-localhost}"
export INDEX_SERVER_PORT="${INDEX_SERVER_PORT:-12345}"
export CHESS_RESULT_ROOT="$OUT_DIR/chess_results"

case "$DATASET" in
  spider)
    export DATA_MODE=dev
    if [ "$LIMIT" -ge 1000000 ]; then
      PREP_LIMIT_ARG=()
      EVAL_LIMIT_ARG=()
    else
      PREP_LIMIT_ARG=(--limit "$LIMIT")
      EVAL_LIMIT_ARG=(--limit "$LIMIT")
    fi
    DATA_PATH="$("$PYTHON_BIN" "$ROOT/scripts/baselines/prepare_chess_inputs.py" \
      --dataset spider \
      --data-root "$DATA_ROOT" \
      --output-root "$ROOT/outputs/rq_final_20260608_023504" \
      "${PREP_LIMIT_ARG[@]}")"
    export DB_ROOT_PATH="$ROOT/outputs/rq_final_20260608_023504/baseline_scratch/chess_spider_dev"
    ;;
  bird)
    export DATA_MODE=dev
    if [ "$LIMIT" -ge 1000000 ]; then
      DATA_PATH="$DATA_ROOT/roots/bird/dev.json"
      EVAL_LIMIT_ARG=()
    else
      DATA_PATH="$("$PYTHON_BIN" "$ROOT/scripts/baselines/prepare_chess_inputs.py" \
        --dataset bird \
        --data-root "$DATA_ROOT" \
        --output-root "$ROOT/outputs/rq_final_20260608_023504" \
        --limit "$LIMIT")"
      EVAL_LIMIT_ARG=(--limit "$LIMIT")
    fi
    export DB_ROOT_PATH="$DATA_ROOT/roots/bird"
    ;;
  *)
    echo "Usage: $0 [spider|bird] [endpoint_port] [limit] [run_id]" >&2
    exit 2
    ;;
esac

export PYTHONPATH="${CHESS_EXTRA_PYTHONPATH:-$ROOT/.baseline_envs/chess_py311_ultra}:$ROOT/external_baselines/CHESS/src"
cd "$ROOT/external_baselines/CHESS"
"$PYTHON_BIN" src/main.py \
  --data_mode "$DATA_MODE" \
  --data_path "$DATA_PATH" \
  --config run/configs/CHESS_SDMC_GEMMA4_SMOKE.yaml \
  --num_workers 1 \
  --log_level info

PRED_JSON="$(find "$CHESS_RESULT_ROOT" -type f -name '*-predictions.json' -printf '%T@ %p\n' | sort -n | tail -1 | cut -d' ' -f2-)"
if [ -z "$PRED_JSON" ] || [ ! -f "$PRED_JSON" ]; then
  echo "CHESS prediction file not found under $CHESS_RESULT_ROOT" >&2
  exit 4
fi

PYTHONPATH="$ROOT/src" "$PYTHON_BIN" "$ROOT/scripts/evaluate_baseline_predictions.py" \
  --dataset "$DATASET" \
  --split dev \
  --root "$DATA_ROOT/roots/$DATASET" \
  --store "$DATA_ROOT/context_stores/${DATASET}_dev_context_store.sqlite" \
  --input "$PRED_JSON" \
  --input-format json_map \
  --output "$OUT_DIR/eval" \
  --condition-id CHESS \
  --model-label gemma4_26b_sdmc \
  "${EVAL_LIMIT_ARG[@]}"

echo "$OUT_DIR"
