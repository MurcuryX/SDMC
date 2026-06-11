#!/bin/bash
set -euo pipefail

ROOT="<SDMC_ROOT>"
if [ "${SDMC_GEMMA4_LOCKED:-0}" != "1" ]; then
  export SDMC_GEMMA4_LOCKED=1
  exec "$ROOT/scripts/baselines/with_gemma4_endpoint_lock.sh" "$0" "$@"
fi
RUN_ID="${1:-macsql_spider_dev3_$(date +%Y%m%d_%H%M%S)}"
OUT_DIR="$ROOT/outputs/rq_final_20260608_023504/baseline_smoke_runs/$RUN_ID"
mkdir -p "$OUT_DIR"

cd "$ROOT"
export PYTHONPATH="$ROOT/.baseline_envs/macsql:$ROOT/external_baselines/MAC-SQL"
export OPENAI_BASE_URL="${OPENAI_BASE_URL:-http://127.0.0.1:18114/v1}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-local-calibration}"
export MACSQL_MODEL="${MACSQL_MODEL:-gemma4_26b_sdmc}"
export MACSQL_TEMPERATURE=0
export MACSQL_MAX_TOKENS=768

python3 external_baselines/MAC-SQL/run.py \
  --dataset_name spider \
  --dataset_mode dev \
  --input_file "$ROOT/outputs/rq_final_20260608_023504/baseline_smoke_data/macsql_spider_dev3.json" \
  --db_path "$ROOT/outputs/rq_final_20260608_023504/local_data/roots/spider/database" \
  --tables_json_path "$ROOT/outputs/rq_final_20260608_023504/local_data/roots/spider/tables.json" \
  --output_file "$OUT_DIR/predictions_raw.jsonl" \
  --log_file "$OUT_DIR/macsql.log"

PYTHONPATH="$ROOT/src" python3 "$ROOT/scripts/evaluate_baseline_predictions.py" \
  --dataset spider \
  --split dev \
  --root "$ROOT/outputs/rq_final_20260608_023504/local_data/roots/spider" \
  --store "$ROOT/outputs/rq_final_20260608_023504/local_data/context_stores/spider_dev_context_store.sqlite" \
  --input "$OUT_DIR/pred_dev.sql" \
  --input-format lines \
  --output "$OUT_DIR/eval" \
  --condition-id MAC_SQL_SMOKE \
  --model-label gemma4_26b_sdmc \
  --limit 3
