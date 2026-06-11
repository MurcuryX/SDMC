#!/bin/bash
set -euo pipefail

ROOT="${SDMC_ROOT:-<SERVER1_SDMC_ROOT>}"
DATA_ROOT="${SDMC_DATA_ROOT:-<SERVER1_DATA_ROOT>/SDMC_remote_run/local_data}"
PYTHON_BIN="${SDMC_CLIENT_PYTHON:-<SERVER1_DATA_ROOT>/SDMC/envs/macsql-client/bin/python}"
DATASET="${1:-spider}"
PORT="${2:-18114}"
LIMIT="${3:-1000000}"
RUN_ID="${4:-dailsql_${DATASET}_gemma4_p${PORT}_$(date +%Y%m%d_%H%M%S)}"

case "$DATASET" in
  spider)
    QUESTION_DIR="$ROOT/outputs/rq_final_20260608_023504/baseline_full_data/dail_spider_dev"
    DB_ROOT="$DATA_ROOT/roots/spider/database"
    ROOT_DIR="$DATA_ROOT/roots/spider"
    STORE="$DATA_ROOT/context_stores/spider_dev_context_store.sqlite"
    EVAL_INPUT="$QUESTION_DIR/RESULTS_MODEL-gemma4_26b_sdmc.txt"
    EVAL_FORMAT="lines"
    ;;
  bird)
    QUESTION_DIR="$ROOT/outputs/rq_final_20260608_023504/baseline_full_data/dail_bird_dev"
    DB_ROOT="$DATA_ROOT/roots/bird/dev_databases"
    ROOT_DIR="$DATA_ROOT/roots/bird"
    STORE="$DATA_ROOT/context_stores/bird_dev_context_store.sqlite"
    EVAL_INPUT="$QUESTION_DIR/RESULTS_MODEL-gemma4_26b_sdmc.json"
    EVAL_FORMAT="json_map"
    ;;
  *)
    echo "Usage: $0 [spider|bird] [endpoint_port] [limit] [run_id]" >&2
    exit 2
    ;;
esac

ENDPOINT="http://127.0.0.1:${PORT}/v1"
if ! curl -fsS "$ENDPOINT/models" >/dev/null; then
  echo "Gemma4 endpoint $ENDPOINT is not reachable." >&2
  exit 3
fi

if [ ! -f "$QUESTION_DIR/questions.json" ]; then
  echo "Missing DAIL question file: $QUESTION_DIR/questions.json" >&2
  exit 4
fi

OUT_DIR="$ROOT/outputs/rq_final_20260608_023504/baseline_runs/$RUN_ID"
mkdir -p "$OUT_DIR"
rm -f "$QUESTION_DIR/RESULTS_MODEL-gemma4_26b_sdmc.txt" "$QUESTION_DIR/RESULTS_MODEL-gemma4_26b_sdmc.json"

{
  echo "run_id=$RUN_ID"
  echo "dataset=$DATASET"
  echo "limit=$LIMIT"
  echo "endpoint=$ENDPOINT"
  echo "model=gemma4_26b_sdmc"
  echo "method=DAIL-SQL"
  echo "question_dir=$QUESTION_DIR"
  echo "data_root=$DATA_ROOT"
  echo "started_at=$(date '+%Y-%m-%d %H:%M:%S')"
} > "$OUT_DIR/run_manifest.txt"

cd "$ROOT"
export PYTHONPATH="$ROOT/external_baselines/DAIL-SQL"
export OPENAI_BASE_URL="$ENDPOINT"
export OPENAI_API_KEY="${OPENAI_API_KEY:-local-calibration}"
export DAIL_MAX_TOKENS="${DAIL_MAX_TOKENS:-768}"

"$PYTHON_BIN" external_baselines/DAIL-SQL/ask_llm.py \
  --question "$QUESTION_DIR" \
  --openai_api_key "$OPENAI_API_KEY" \
  --model gemma4_26b_sdmc \
  --start_index 0 \
  --end_index "$LIMIT" \
  --temperature 0 \
  --batch_size 1 \
  --n 1 \
  --db_dir "$DB_ROOT"

if [ "$DATASET" = "bird" ]; then
  "$PYTHON_BIN" external_baselines/DAIL-SQL/to_bird_output.py \
    --dail_output "$QUESTION_DIR/RESULTS_MODEL-gemma4_26b_sdmc.txt" \
    --bird_dev "$DATA_ROOT/roots/bird/dev.json"
fi

cp "$QUESTION_DIR/RESULTS_MODEL-gemma4_26b_sdmc.txt" "$OUT_DIR/raw_predictions.txt"
if [ "$DATASET" = "bird" ]; then
  cp "$QUESTION_DIR/RESULTS_MODEL-gemma4_26b_sdmc.json" "$OUT_DIR/raw_predictions.json"
fi

PYTHONPATH="$ROOT/src" "$PYTHON_BIN" "$ROOT/scripts/evaluate_baseline_predictions.py" \
  --dataset "$DATASET" \
  --split dev \
  --root "$ROOT_DIR" \
  --store "$STORE" \
  --input "$EVAL_INPUT" \
  --input-format "$EVAL_FORMAT" \
  --output "$OUT_DIR/eval" \
  --condition-id "DAIL_SQL" \
  --model-label gemma4_26b_sdmc \
  --limit "$LIMIT"

echo "finished_at=$(date '+%Y-%m-%d %H:%M:%S')" >> "$OUT_DIR/run_manifest.txt"
echo "$OUT_DIR"
