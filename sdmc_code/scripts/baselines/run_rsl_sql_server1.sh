#!/bin/bash
set -euo pipefail

ROOT="${SDMC_ROOT:-<SERVER1_SDMC_ROOT>}"
DATA_ROOT="${SDMC_DATA_ROOT:-<SERVER1_DATA_ROOT>/SDMC_remote_run/local_data}"
PYTHON_BIN="${RSL_SQL_PYTHON:-<SERVER1_DATA_ROOT>/SDMC/envs/macsql-client/bin/python}"
DATASET="${1:-bird}"
PORT="${2:-18114}"
LIMIT="${3:-1000000}"
RUN_ID="${4:-rsl_sql_${DATASET}_gemma4_p${PORT}_$(date +%Y%m%d_%H%M%S)}"
KSHOT="${RSL_SQL_KSHOT:-3}"
OUT_DIR="$ROOT/outputs/rq_final_20260608_023504/baseline_runs/$RUN_ID"
SCRATCH_ROOT="$ROOT/outputs/rq_final_20260608_023504/baseline_scratch"
WORK_PARENT="$OUT_DIR/work"
WORK_DIR="$WORK_PARENT/RSL-SQL"

mkdir -p "$OUT_DIR" "$WORK_PARENT" "$SCRATCH_ROOT"
if [ -e "$WORK_DIR" ]; then
  echo "Work directory already exists: $WORK_DIR" >&2
  exit 7
fi

ENDPOINT="http://127.0.0.1:${PORT}/v1"
if ! curl -fsS "$ENDPOINT/models" >/dev/null; then
  echo "Gemma4 endpoint $ENDPOINT is not reachable." >&2
  exit 3
fi

case "$DATASET" in
  spider|bird) ;;
  *)
    echo "Usage: $0 [spider|bird] [endpoint_port] [limit] [run_id]" >&2
    exit 2
    ;;
esac

if [ "$LIMIT" -ge 1000000 ]; then
  PREP_LIMIT_ARG=()
  EVAL_LIMIT_ARG=()
else
  PREP_LIMIT_ARG=(--limit "$LIMIT")
  EVAL_LIMIT_ARG=(--limit "$LIMIT")
fi

PREP_JSON="$("$PYTHON_BIN" "$ROOT/scripts/baselines/prepare_rsl_sql_inputs.py" \
  --dataset "$DATASET" \
  --data-root "$DATA_ROOT" \
  --scratch-root "$SCRATCH_ROOT" \
  "${PREP_LIMIT_ARG[@]}")"
DEV_JSON="$(PREP_JSON="$PREP_JSON" "$PYTHON_BIN" -c 'import json, os; print(json.loads(os.environ["PREP_JSON"])["dev_json"])')"
DB_ROOT="$(PREP_JSON="$PREP_JSON" "$PYTHON_BIN" -c 'import json, os; print(json.loads(os.environ["PREP_JSON"])["db_root"])')"

cp -a "$ROOT/external_baselines/RSL-SQL" "$WORK_DIR"
cd "$WORK_DIR"
mkdir -p data database src/information src/sql_log src/schema_linking
ln -s "$DEV_JSON" data/dev.json
ln -s "$DB_ROOT" database/dev_databases
if [ -n "${RSL_SQL_COLUMN_MEANING:-}" ] && [ -f "$RSL_SQL_COLUMN_MEANING" ]; then
  ln -s "$RSL_SQL_COLUMN_MEANING" data/column_meaning.json
else
  printf '{}\n' > data/column_meaning.json
fi

export OPENAI_API_KEY="${OPENAI_API_KEY:-local-calibration}"
export OPENAI_BASE_URL="$ENDPOINT"
export RSL_SQL_BASE_URL="$ENDPOINT"
export RSL_SQL_MODEL="${RSL_SQL_MODEL:-gemma4_26b_sdmc}"
export RSL_SQL_DEV_DATABASES_PATH="$WORK_DIR/database/dev_databases"
export RSL_SQL_DEV_JSON_PATH="$WORK_DIR/data/dev.json"
export TOKENIZERS_PARALLELISM=false
export PYTHONPATH="$WORK_DIR/src:$WORK_DIR/few_shot:${PYTHONPATH:-}"

"$PYTHON_BIN" src/data_construct.py
"$PYTHON_BIN" few_shot/construct_QA.py
"$PYTHON_BIN" few_shot/slg_main.py \
  --dataset src/information/ppl_dev.json \
  --out_file src/information/example.json \
  --kshot "$KSHOT"
"$PYTHON_BIN" src/information/add_example.py

"$PYTHON_BIN" src/step_1_preliminary_sql.py \
  --ppl_file src/information/ppl_dev.json \
  --sql_out_file src/sql_log/preliminary_sql.txt \
  --Schema_linking_LLM src/schema_linking/LLM.json \
  --start_index 0

"$PYTHON_BIN" src/bid_schema_linking.py \
  --pre_sql_file src/sql_log/preliminary_sql.txt \
  --sql_sl_output src/schema_linking/sql.json \
  --hint_sl_output src/schema_linking/hint.json \
  --LLM_sl_output src/schema_linking/LLM.json \
  --Schema_linking_output src/schema_linking/schema.json
cp src/schema_linking/schema.json src/information/schema.json
"$PYTHON_BIN" src/information/add_sl.py

"$PYTHON_BIN" src/step_2_information_augmentation.py \
  --ppl_file src/information/ppl_dev.json \
  --sql_2_output src/sql_log/step_2_information_augmentation.txt \
  --information_output src/information/augmentation.json \
  --start_index 0
"$PYTHON_BIN" src/information/add_augmentation.py

"$PYTHON_BIN" src/step_3_binary_selection.py \
  --ppl_file src/information/ppl_dev.json \
  --sql_3_output src/sql_log/step_3_binary.txt \
  --sql_1 src/sql_log/preliminary_sql.txt \
  --sql_2 src/sql_log/step_2_information_augmentation.txt \
  --start_index 0

"$PYTHON_BIN" src/step_4_self_correction.py \
  --ppl_file src/information/ppl_dev.json \
  --sql_4_output src/sql_log/final_sql.txt \
  --sql_refinement src/sql_log/step_3_binary.txt \
  --start_index 0

cp src/sql_log/final_sql.txt "$OUT_DIR/final_sql.txt"

PYTHONPATH="$ROOT/src" "$PYTHON_BIN" "$ROOT/scripts/evaluate_baseline_predictions.py" \
  --dataset "$DATASET" \
  --split dev \
  --root "$DATA_ROOT/roots/$DATASET" \
  --store "$DATA_ROOT/context_stores/${DATASET}_dev_context_store.sqlite" \
  --input "$OUT_DIR/final_sql.txt" \
  --input-format lines \
  --output "$OUT_DIR/eval" \
  --condition-id RSL_SQL \
  --model-label gemma4_26b_sdmc \
  "${EVAL_LIMIT_ARG[@]}"

echo "$OUT_DIR"
