#!/bin/bash
set -euo pipefail

ROOT="<SDMC_ROOT>"
if [ "${SDMC_GEMMA4_LOCKED:-0}" != "1" ]; then
  export SDMC_GEMMA4_LOCKED=1
  exec "$ROOT/scripts/baselines/with_gemma4_endpoint_lock.sh" "$0" "$@"
fi
RUN_ID="${1:-dinsql_bird_dev3_$(date +%Y%m%d_%H%M%S)}"
OUT_DIR="$ROOT/outputs/rq_final_20260608_023504/baseline_smoke_runs/$RUN_ID"
mkdir -p "$OUT_DIR"

cd "$ROOT"
export PYTHONPATH="$ROOT/.baseline_envs/dinsql_smoke"
export OPENAI_BASE_URL="${OPENAI_BASE_URL:-http://127.0.0.1:18114/v1}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-local-calibration}"
export DIN_BIRD_MODEL="${DIN_BIRD_MODEL:-gemma4_26b_sdmc}"
export DIN_BIRD_DB_PATH="$ROOT/outputs/rq_final_20260608_023504/local_data/roots/bird/dev_databases"
export DIN_BIRD_DEV_JSON="$ROOT/outputs/rq_final_20260608_023504/baseline_smoke_data/din_bird_dev3/dev.json"
export DIN_BIRD_OUTPUT_JSON="$OUT_DIR/predict_dev.json"
export DIN_BIRD_LOGS_CSV="$OUT_DIR/logs.csv"
export DIN_BIRD_START_INDEX=0
export DIN_BIRD_END_INDEX=3

python3 external_baselines/DIN-SQL/DIN-SQL_BIRD.py

PYTHONPATH="$ROOT/src" python3 "$ROOT/scripts/evaluate_baseline_predictions.py" \
  --dataset bird \
  --split dev \
  --root "$ROOT/outputs/rq_final_20260608_023504/local_data/roots/bird" \
  --store "$ROOT/outputs/rq_final_20260608_023504/local_data/context_stores/bird_dev_context_store.sqlite" \
  --input "$OUT_DIR/predict_dev.json" \
  --input-format json_map \
  --output "$OUT_DIR/eval" \
  --condition-id DIN_SQL_BIRD_SMOKE \
  --model-label gemma4_26b_sdmc \
  --limit 3
