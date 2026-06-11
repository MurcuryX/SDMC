#!/bin/bash
set -euo pipefail

ROOT="${SDMC_ROOT:-<SERVER1_SDMC_ROOT>}"
DATA_ROOT="${SDMC_DATA_ROOT:-<SERVER1_DATA_ROOT>/SDMC_remote_run/local_data}"
PYTHON_BIN="${SDMC_CLIENT_PYTHON:-<SERVER1_DATA_ROOT>/SDMC/envs/macsql-client/bin/python}"
CONTEXT_KIND="${1:-schema}"  # schema | llm | sql
PORT="${2:-18114}"
RUN_ID="${3:-macsql_rq3_spider_${CONTEXT_KIND}_gemma4_p${PORT}_$(date +%Y%m%d_%H%M%S)}"
INPUT_DIR="${RQ3_INPUT_DIR:-$ROOT/outputs/rq_final_20260608_023504/rq3_inputs}"
INPUT="$INPUT_DIR/macsql_spider_rq3_${CONTEXT_KIND}.json"
OUT_DIR="$ROOT/outputs/rq_final_20260608_023504/rq3_runs/$RUN_ID"
mkdir -p "$OUT_DIR"

case "$CONTEXT_KIND" in
  schema|llm|sql) ;;
  *)
    echo "Usage: $0 [schema|llm|sql] [port] [run_id]" >&2
    exit 2
    ;;
esac

ENDPOINT="http://127.0.0.1:${PORT}/v1"
if ! curl -fsS "$ENDPOINT/models" >/dev/null; then
  echo "Gemma4 endpoint $ENDPOINT is not reachable." >&2
  exit 3
fi

cd "$ROOT"
export PYTHONPATH="$ROOT/external_baselines/MAC-SQL"
export OPENAI_BASE_URL="$ENDPOINT"
export OPENAI_API_KEY="${OPENAI_API_KEY:-local-calibration}"
export MACSQL_MODEL="${MACSQL_MODEL:-gemma4_26b_sdmc}"
export MACSQL_TEMPERATURE=0
if [ "$CONTEXT_KIND" = "sql" ] || [ "$CONTEXT_KIND" = "llm" ]; then
  export MACSQL_MAX_TOKENS="${MACSQL_MAX_TOKENS:-256}"
else
  export MACSQL_MAX_TOKENS="${MACSQL_MAX_TOKENS:-768}"
fi

"$PYTHON_BIN" external_baselines/MAC-SQL/run.py \
  --dataset_name spider \
  --dataset_mode dev \
  --input_file "$INPUT" \
  --db_path "$DATA_ROOT/roots/spider/database" \
  --tables_json_path "$DATA_ROOT/roots/spider/tables.json" \
  --output_file "$OUT_DIR/predictions_raw.jsonl" \
  --log_file "$OUT_DIR/macsql.log"

PYTHONPATH="$ROOT/src" "$PYTHON_BIN" "$ROOT/scripts/evaluate_baseline_predictions.py" \
  --dataset spider \
  --split dev \
  --root "$DATA_ROOT/roots/spider" \
  --store "$DATA_ROOT/context_stores/spider_dev_context_store.sqlite" \
  --input "$OUT_DIR/pred_dev.sql" \
  --input-format lines \
  --output "$OUT_DIR/eval" \
  --condition-id "RQ3_MACSQL_${CONTEXT_KIND}" \
  --model-label gemma4_26b_sdmc

echo "$OUT_DIR"
