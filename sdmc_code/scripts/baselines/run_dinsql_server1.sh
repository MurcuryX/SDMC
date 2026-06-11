#!/bin/bash
set -euo pipefail

ROOT="${SDMC_ROOT:-<SERVER1_SDMC_ROOT>}"
DATA_ROOT="${SDMC_DATA_ROOT:-<SERVER1_DATA_ROOT>/SDMC_remote_run/local_data}"
PYTHON_BIN="${SDMC_CLIENT_PYTHON:-<SERVER1_DATA_ROOT>/SDMC/envs/macsql-client/bin/python}"
DATASET="${1:-spider}"
PORT="${2:-18114}"
LIMIT="${3:-1000000}"
RUN_ID="${4:-dinsql_${DATASET}_gemma4_p${PORT}_$(date +%Y%m%d_%H%M%S)}"
OUT_DIR="$ROOT/outputs/rq_final_20260608_023504/baseline_runs/$RUN_ID"
mkdir -p "$OUT_DIR"

ENDPOINT="http://127.0.0.1:${PORT}/v1"
if ! curl -fsS "$ENDPOINT/models" >/dev/null; then
  echo "Gemma4 endpoint $ENDPOINT is not reachable." >&2
  exit 3
fi

cd "$ROOT"
export PYTHONPATH="${DINSQL_EXTRA_PYTHONPATH:+$DINSQL_EXTRA_PYTHONPATH:}$ROOT/external_baselines/DIN-SQL"
export OPENAI_BASE_URL="$ENDPOINT"
export OPENAI_API_KEY="${OPENAI_API_KEY:-local-calibration}"

case "$DATASET" in
  spider)
    export DINSQL_MODEL="${DINSQL_MODEL:-gemma4_26b_sdmc}"
    export DIN_START_INDEX=0
    export DIN_END_INDEX="$LIMIT"
    "$PYTHON_BIN" external_baselines/DIN-SQL/DIN-SQL.py \
      --dataset "$DATA_ROOT/roots/spider/" \
      --output "$OUT_DIR/predicted_sql.txt"
    PYTHONPATH="$ROOT/src" "$PYTHON_BIN" "$ROOT/scripts/evaluate_baseline_predictions.py" \
      --dataset spider \
      --split dev \
      --root "$DATA_ROOT/roots/spider" \
      --store "$DATA_ROOT/context_stores/spider_dev_context_store.sqlite" \
      --input "$OUT_DIR/predicted_sql.txt" \
      --input-format lines \
      --output "$OUT_DIR/eval" \
      --condition-id DIN_SQL \
      --model-label gemma4_26b_sdmc \
      --limit "$LIMIT"
    ;;
  bird)
    export DIN_BIRD_MODEL="${DIN_BIRD_MODEL:-gemma4_26b_sdmc}"
    export DIN_BIRD_DB_PATH="$DATA_ROOT/roots/bird/dev_databases"
    export DIN_BIRD_DEV_JSON="$DATA_ROOT/roots/bird/dev.json"
    export DIN_BIRD_OUTPUT_JSON="$OUT_DIR/predict_dev.json"
    export DIN_BIRD_LOGS_CSV="$OUT_DIR/logs.csv"
    export DIN_BIRD_START_INDEX=0
    export DIN_BIRD_END_INDEX="$LIMIT"
    "$PYTHON_BIN" external_baselines/DIN-SQL/DIN-SQL_BIRD.py
    PYTHONPATH="$ROOT/src" "$PYTHON_BIN" "$ROOT/scripts/evaluate_baseline_predictions.py" \
      --dataset bird \
      --split dev \
      --root "$DATA_ROOT/roots/bird" \
      --store "$DATA_ROOT/context_stores/bird_dev_context_store.sqlite" \
      --input "$OUT_DIR/predict_dev.json" \
      --input-format json_map \
      --output "$OUT_DIR/eval" \
      --condition-id DIN_SQL \
      --model-label gemma4_26b_sdmc \
      --limit "$LIMIT"
    ;;
  *)
    echo "Usage: $0 [spider|bird] [endpoint_port] [limit] [run_id]" >&2
    exit 2
    ;;
esac

echo "$OUT_DIR"
