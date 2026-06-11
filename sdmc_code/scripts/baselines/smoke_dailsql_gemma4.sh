#!/bin/bash
set -euo pipefail

ROOT="<SDMC_ROOT>"
if [ "${SDMC_GEMMA4_LOCKED:-0}" != "1" ]; then
  export SDMC_GEMMA4_LOCKED=1
  exec "$ROOT/scripts/baselines/with_gemma4_endpoint_lock.sh" "$0" "$@"
fi
QUESTION_DIR="${1:-$ROOT/outputs/rq_final_20260608_023504/baseline_smoke_data/dail_spider_dev3}"

cd "$ROOT"
export PYTHONPATH="$ROOT/.baseline_envs/dailsql_smoke:$ROOT/external_baselines/DAIL-SQL"
export OPENAI_BASE_URL="${OPENAI_BASE_URL:-http://127.0.0.1:18114/v1}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-local-calibration}"
export DAIL_MAX_TOKENS=768

python3 external_baselines/DAIL-SQL/ask_llm.py \
  --question "$QUESTION_DIR" \
  --openai_api_key "$OPENAI_API_KEY" \
  --model gemma4_26b_sdmc \
  --start_index 0 \
  --end_index 3 \
  --temperature 0 \
  --batch_size 1 \
  --n 1 \
  --db_dir "$ROOT/outputs/rq_final_20260608_023504/local_data/roots/spider/database"

OUT_DIR="$ROOT/outputs/rq_final_20260608_023504/baseline_smoke_runs/dailsql_spider_dev3"
mkdir -p "$OUT_DIR"
PYTHONPATH="$ROOT/src" python3 "$ROOT/scripts/evaluate_baseline_predictions.py" \
  --dataset spider \
  --split dev \
  --root "$ROOT/outputs/rq_final_20260608_023504/local_data/roots/spider" \
  --store "$ROOT/outputs/rq_final_20260608_023504/local_data/context_stores/spider_dev_context_store.sqlite" \
  --input "$QUESTION_DIR/RESULTS_MODEL-gemma4_26b_sdmc.txt" \
  --input-format lines \
  --output "$OUT_DIR/eval" \
  --condition-id DAIL_SQL_SMOKE \
  --model-label gemma4_26b_sdmc \
  --limit 3
