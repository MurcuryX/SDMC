#!/bin/bash
set -euo pipefail

ROOT="<SDMC_ROOT>"
if [ "${SDMC_GEMMA4_LOCKED:-0}" != "1" ]; then
  export SDMC_GEMMA4_LOCKED=1
  exec "$ROOT/scripts/baselines/with_gemma4_endpoint_lock.sh" "$0" "$@"
fi
DATASET="${1:-spider}"  # spider | bird
RUN_ID="${2:-macsql_${DATASET}_dev_full_$(date +%Y%m%d_%H%M%S)}"
OUT_DIR="$ROOT/outputs/rq_final_20260608_023504/baseline_runs/$RUN_ID"
mkdir -p "$OUT_DIR"

case "$DATASET" in
  spider)
    INPUT="$ROOT/outputs/rq_final_20260608_023504/local_data/roots/spider/dev.json"
    DB_ROOT="$ROOT/outputs/rq_final_20260608_023504/local_data/roots/spider/database"
    TABLES="$ROOT/outputs/rq_final_20260608_023504/local_data/roots/spider/tables.json"
    EVAL_INPUT="$OUT_DIR/pred_dev.sql"
    EVAL_FORMAT="lines"
    STORE="$ROOT/outputs/rq_final_20260608_023504/local_data/context_stores/spider_dev_context_store.sqlite"
    ROOT_DIR="$ROOT/outputs/rq_final_20260608_023504/local_data/roots/spider"
    ;;
  bird)
    INPUT="$ROOT/outputs/rq_final_20260608_023504/local_data/roots/bird/dev.json"
    DB_ROOT="$ROOT/outputs/rq_final_20260608_023504/local_data/roots/bird/dev_databases"
    TABLES="$ROOT/outputs/rq_final_20260608_023504/local_data/roots/bird/dev_tables.json"
    EVAL_INPUT="$OUT_DIR/predict_dev.json"
    EVAL_FORMAT="json_map"
    STORE="$ROOT/outputs/rq_final_20260608_023504/local_data/context_stores/bird_dev_context_store.sqlite"
    ROOT_DIR="$ROOT/outputs/rq_final_20260608_023504/local_data/roots/bird"
    ;;
  *)
    echo "Usage: $0 [spider|bird] [run_id]" >&2
    exit 2
    ;;
esac

if ! curl -fsS http://127.0.0.1:18114/v1/models >/dev/null; then
  echo "Gemma4 endpoint http://127.0.0.1:18114/v1 is not reachable." >&2
  exit 3
fi

cd "$ROOT"
export PYTHONPATH="$ROOT/.baseline_envs/macsql:$ROOT/external_baselines/MAC-SQL"
export OPENAI_BASE_URL="${OPENAI_BASE_URL:-http://127.0.0.1:18114/v1}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-local-calibration}"
export MACSQL_MODEL="${MACSQL_MODEL:-gemma4_26b_sdmc}"
export MACSQL_TEMPERATURE=0
export MACSQL_MAX_TOKENS="${MACSQL_MAX_TOKENS:-768}"

python3 external_baselines/MAC-SQL/run.py \
  --dataset_name "$DATASET" \
  --dataset_mode dev \
  --input_file "$INPUT" \
  --db_path "$DB_ROOT" \
  --tables_json_path "$TABLES" \
  --output_file "$OUT_DIR/predictions_raw.jsonl" \
  --log_file "$OUT_DIR/macsql.log"

PYTHONPATH="$ROOT/src" python3 "$ROOT/scripts/evaluate_baseline_predictions.py" \
  --dataset "$DATASET" \
  --split dev \
  --root "$ROOT_DIR" \
  --store "$STORE" \
  --input "$EVAL_INPUT" \
  --input-format "$EVAL_FORMAT" \
  --output "$OUT_DIR/eval" \
  --condition-id "MAC_SQL" \
  --model-label gemma4_26b_sdmc
