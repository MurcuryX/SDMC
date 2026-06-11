#!/bin/bash
set -euo pipefail

ROOT="<SDMC_ROOT>"
if [ "${SDMC_GEMMA4_LOCKED:-0}" != "1" ]; then
  export SDMC_GEMMA4_LOCKED=1
  exec "$ROOT/scripts/baselines/with_gemma4_endpoint_lock.sh" "$0" "$@"
fi
RUN_ID="${1:-dinsql_spider_dev3_$(date +%Y%m%d_%H%M%S)}"
OUT_DIR="$ROOT/outputs/rq_final_20260608_023504/baseline_smoke_runs/$RUN_ID"
mkdir -p "$OUT_DIR"

cd "$ROOT"
export PYTHONPATH="$ROOT/.baseline_envs/dinsql_smoke"
export OPENAI_BASE_URL="${OPENAI_BASE_URL:-http://127.0.0.1:18114/v1}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-local-calibration}"
export DINSQL_MODEL="${DINSQL_MODEL:-gemma4_26b_sdmc}"
export DIN_START_INDEX=0
export DIN_END_INDEX=3

python3 external_baselines/DIN-SQL/DIN-SQL.py \
  --dataset "$ROOT/outputs/rq_final_20260608_023504/baseline_smoke_data/din_spider_dev3/" \
  --output "$OUT_DIR/predicted_sql.txt"

PYTHONPATH="$ROOT/src" python3 "$ROOT/scripts/evaluate_baseline_predictions.py" \
  --dataset spider \
  --split dev \
  --root "$ROOT/outputs/rq_final_20260608_023504/local_data/roots/spider" \
  --store "$ROOT/outputs/rq_final_20260608_023504/local_data/context_stores/spider_dev_context_store.sqlite" \
  --input "$OUT_DIR/predicted_sql.txt" \
  --input-format lines \
  --output "$OUT_DIR/eval" \
  --condition-id DIN_SQL_SMOKE \
  --model-label gemma4_26b_sdmc \
  --limit 3
