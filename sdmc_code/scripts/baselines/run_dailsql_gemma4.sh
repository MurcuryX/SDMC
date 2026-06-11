#!/bin/bash
set -euo pipefail

ROOT="<SDMC_ROOT>"
if [ "${SDMC_GEMMA4_LOCKED:-0}" != "1" ]; then
  export SDMC_GEMMA4_LOCKED=1
  exec "$ROOT/scripts/baselines/with_gemma4_endpoint_lock.sh" "$0" "$@"
fi
DATASET="${1:-spider}"  # spider | bird

case "$DATASET" in
  spider)
    QUESTION_DIR="$ROOT/outputs/rq_final_20260608_023504/baseline_full_data/dail_spider_dev"
    DB_ROOT="$ROOT/outputs/rq_final_20260608_023504/local_data/roots/spider/database"
    ROOT_DIR="$ROOT/outputs/rq_final_20260608_023504/local_data/roots/spider"
    STORE="$ROOT/outputs/rq_final_20260608_023504/local_data/context_stores/spider_dev_context_store.sqlite"
    EVAL_INPUT="$QUESTION_DIR/RESULTS_MODEL-gemma4_26b_sdmc.txt"
    EVAL_FORMAT="lines"
    ;;
  bird)
    QUESTION_DIR="$ROOT/outputs/rq_final_20260608_023504/baseline_full_data/dail_bird_dev"
    DB_ROOT="$ROOT/outputs/rq_final_20260608_023504/local_data/roots/bird/dev_databases"
    ROOT_DIR="$ROOT/outputs/rq_final_20260608_023504/local_data/roots/bird"
    STORE="$ROOT/outputs/rq_final_20260608_023504/local_data/context_stores/bird_dev_context_store.sqlite"
    EVAL_INPUT="$QUESTION_DIR/RESULTS_MODEL-gemma4_26b_sdmc.json"
    EVAL_FORMAT="json_map"
    ;;
  *)
    echo "Usage: $0 [spider|bird]" >&2
    exit 2
    ;;
esac

if ! curl -fsS http://127.0.0.1:18114/v1/models >/dev/null; then
  echo "Gemma4 endpoint http://127.0.0.1:18114/v1 is not reachable." >&2
  exit 3
fi

cd "$ROOT"
export PYTHONPATH="$ROOT/.baseline_envs/dailsql_smoke:$ROOT/external_baselines/DAIL-SQL"
export OPENAI_BASE_URL="${OPENAI_BASE_URL:-http://127.0.0.1:18114/v1}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-local-calibration}"
export DAIL_MAX_TOKENS="${DAIL_MAX_TOKENS:-768}"

python3 external_baselines/DAIL-SQL/ask_llm.py \
  --question "$QUESTION_DIR" \
  --openai_api_key "$OPENAI_API_KEY" \
  --model gemma4_26b_sdmc \
  --start_index "${DAIL_START_INDEX:-0}" \
  --end_index "${DAIL_END_INDEX:-1000000}" \
  --temperature 0 \
  --batch_size 1 \
  --n 1 \
  --db_dir "$DB_ROOT"

if [ "$DATASET" = "bird" ]; then
  python3 external_baselines/DAIL-SQL/to_bird_output.py \
    --dail_output "$QUESTION_DIR/RESULTS_MODEL-gemma4_26b_sdmc.txt" \
    --bird_dev "$ROOT/outputs/rq_final_20260608_023504/local_data/roots/bird/dev.json"
fi

OUT_DIR="$ROOT/outputs/rq_final_20260608_023504/baseline_runs/dailsql_${DATASET}_dev_full_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUT_DIR"
cp "$QUESTION_DIR/RESULTS_MODEL-gemma4_26b_sdmc.txt" "$OUT_DIR/raw_predictions.txt"
if [ "$DATASET" = "bird" ]; then
  cp "$QUESTION_DIR/RESULTS_MODEL-gemma4_26b_sdmc.json" "$OUT_DIR/raw_predictions.json"
fi
PYTHONPATH="$ROOT/src" python3 "$ROOT/scripts/evaluate_baseline_predictions.py" \
  --dataset "$DATASET" \
  --split dev \
  --root "$ROOT_DIR" \
  --store "$STORE" \
  --input "$EVAL_INPUT" \
  --input-format "$EVAL_FORMAT" \
  --output "$OUT_DIR/eval" \
  --condition-id "DAIL_SQL" \
  --model-label gemma4_26b_sdmc
