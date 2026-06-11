#!/bin/bash
set -euo pipefail

ROOT="${SDMC_ROOT:-<SERVER1_SDMC_ROOT>}"
DATA_ROOT="${SDMC_DATA_ROOT:-$ROOT/outputs/rq_final_20260608_023504/local_data}"
PYTHON_BIN="${SDMC_CLIENT_PYTHON:-python3}"
DATASET="${1:-spider}"        # spider | bird
PORT="${2:-18114}"            # <gpu-alias>-local vLLM endpoint port
LIMIT="${3:-50}"
RUN_ID="${4:-macsql_${DATASET}_dual_pilot_p${PORT}_n${LIMIT}_$(date +%Y%m%d_%H%M%S)}"
OUT_DIR="$ROOT/outputs/rq_final_20260608_023504/baseline_runs/$RUN_ID"
mkdir -p "$OUT_DIR"

case "$DATASET" in
  spider)
    FULL_INPUT="$DATA_ROOT/roots/spider/dev.json"
    DB_ROOT="$DATA_ROOT/roots/spider/database"
    TABLES="$DATA_ROOT/roots/spider/tables.json"
    EVAL_INPUT="$OUT_DIR/pred_dev.sql"
    EVAL_FORMAT="lines"
    STORE="$DATA_ROOT/context_stores/spider_dev_context_store.sqlite"
    ROOT_DIR="$DATA_ROOT/roots/spider"
    ;;
  bird)
    FULL_INPUT="$DATA_ROOT/roots/bird/dev.json"
    DB_ROOT="$DATA_ROOT/roots/bird/dev_databases"
    TABLES="$DATA_ROOT/roots/bird/dev_tables.json"
    EVAL_INPUT="$OUT_DIR/predict_dev.json"
    EVAL_FORMAT="json_map"
    STORE="$DATA_ROOT/context_stores/bird_dev_context_store.sqlite"
    ROOT_DIR="$DATA_ROOT/roots/bird"
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

PILOT_INPUT="$OUT_DIR/input_first_${LIMIT}.json"
"$PYTHON_BIN" - "$FULL_INPUT" "$PILOT_INPUT" "$LIMIT" <<'PY'
import json
import sys
src, dst, limit_s = sys.argv[1:]
limit = int(limit_s)
data = json.load(open(src, encoding="utf-8"))
if isinstance(data, dict):
    for key in ("data", "questions", "examples"):
        if isinstance(data.get(key), list):
            data[key] = data[key][:limit]
            break
    else:
        raise SystemExit(f"Unsupported dict input shape: {src}")
else:
    data = data[:limit]
with open(dst, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
PY

{
  echo "run_id=$RUN_ID"
  echo "dataset=$DATASET"
  echo "limit=$LIMIT"
  echo "endpoint=$ENDPOINT"
  echo "model=gemma4_26b_sdmc"
  echo "method=MAC-SQL"
  echo "input=$PILOT_INPUT"
  echo "started_at=$(date '+%Y-%m-%d %H:%M:%S')"
} > "$OUT_DIR/run_manifest.txt"

cd "$ROOT"
export PYTHONPATH="$ROOT/external_baselines/MAC-SQL"
export OPENAI_BASE_URL="$ENDPOINT"
export OPENAI_API_KEY="${OPENAI_API_KEY:-local-calibration}"
export MACSQL_MODEL="${MACSQL_MODEL:-gemma4_26b_sdmc}"
export MACSQL_TEMPERATURE=0
export MACSQL_MAX_TOKENS="${MACSQL_MAX_TOKENS:-768}"

"$PYTHON_BIN" external_baselines/MAC-SQL/run.py \
  --dataset_name "$DATASET" \
  --dataset_mode dev \
  --input_file "$PILOT_INPUT" \
  --db_path "$DB_ROOT" \
  --tables_json_path "$TABLES" \
  --output_file "$OUT_DIR/predictions_raw.jsonl" \
  --log_file "$OUT_DIR/macsql.log"

PYTHONPATH="$ROOT/src" "$PYTHON_BIN" "$ROOT/scripts/evaluate_baseline_predictions.py" \
  --dataset "$DATASET" \
  --split dev \
  --root "$ROOT_DIR" \
  --store "$STORE" \
  --input "$EVAL_INPUT" \
  --input-format "$EVAL_FORMAT" \
  --output "$OUT_DIR/eval" \
  --condition-id "MAC_SQL_DUAL_PILOT" \
  --model-label gemma4_26b_sdmc \
  --limit "$LIMIT"

echo "finished_at=$(date '+%Y-%m-%d %H:%M:%S')" >> "$OUT_DIR/run_manifest.txt"
echo "$OUT_DIR"
