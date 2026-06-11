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
    CONFIG="$ROOT/external_baselines/DeepEye-SQL/config/config-sdmc-gemma4-spider-smoke.toml"
    SNAPSHOT="$ROOT/outputs/rq_final_20260608_023504/baseline_smoke_runs/deepeye_spider_dev3/workspace/sql_selection/spider_dev.snapshot"
    PRED_JSON="$ROOT/outputs/rq_final_20260608_023504/baseline_smoke_runs/deepeye_spider_dev3/predictions.json"
    EVAL_OUT="$ROOT/outputs/rq_final_20260608_023504/baseline_smoke_runs/deepeye_spider_dev3/eval"
    ROOT_DIR="$ROOT/outputs/rq_final_20260608_023504/local_data/roots/spider"
    STORE="$ROOT/outputs/rq_final_20260608_023504/local_data/context_stores/spider_dev_context_store.sqlite"
    ;;
  bird)
    CONFIG="$ROOT/external_baselines/DeepEye-SQL/config/config-sdmc-gemma4-bird-smoke.toml"
    SNAPSHOT="$ROOT/outputs/rq_final_20260608_023504/baseline_smoke_runs/deepeye_bird_dev3/workspace/sql_selection/bird_dev.snapshot"
    PRED_JSON="$ROOT/outputs/rq_final_20260608_023504/baseline_smoke_runs/deepeye_bird_dev3/predictions.json"
    EVAL_OUT="$ROOT/outputs/rq_final_20260608_023504/baseline_smoke_runs/deepeye_bird_dev3/eval"
    ROOT_DIR="$ROOT/outputs/rq_final_20260608_023504/local_data/roots/bird"
    STORE="$ROOT/outputs/rq_final_20260608_023504/local_data/context_stores/bird_dev_context_store.sqlite"
    ;;
  *)
    echo "Usage: $0 [spider|bird]" >&2
    exit 2
    ;;
esac

EMBED_PATH=$(awk -F'=' '
  $0 ~ /^\[vector_database\]/ {in_section=1; next}
  $0 ~ /^\[/ && in_section {exit}
  in_section && $1 ~ /embedding_model_name_or_path/ {
    gsub(/^[ \t"]+|[ \t"]+$/, "", $2)
    print $2
    exit
  }
' "$CONFIG")
if [ ! -e "$EMBED_PATH" ] || echo "$EMBED_PATH" | grep -q "MISSING_EMBEDDING_MODEL"; then
  echo "DeepEye embedding backend is not configured: $EMBED_PATH" >&2
  echo "Set vector_database.embedding_model_name_or_path to a confirmed local embedding model before running." >&2
  exit 5
fi
if ! curl -fsS http://127.0.0.1:18114/v1/models >/dev/null; then
  echo "Gemma4 endpoint http://127.0.0.1:18114/v1 is not reachable." >&2
  exit 3
fi

cd "$ROOT/external_baselines/DeepEye-SQL"
export CONFIG_PATH="$CONFIG"
export UV_PYTHON_INSTALL_DIR="$ROOT/.baseline_envs/uv_pythons"
export UV_PROJECT_ENVIRONMENT="$ROOT/.baseline_envs/deepeye_venv"
"$ROOT/.baseline_envs/uv_tool/bin/uv" run runner/preprocess_dataset.py
"$ROOT/.baseline_envs/uv_tool/bin/uv" run runner/create_vector_db_parallel.py
"$ROOT/.baseline_envs/uv_tool/bin/uv" run runner/run_value_retrieval.py
"$ROOT/.baseline_envs/uv_tool/bin/uv" run runner/run_schema_linking.py
"$ROOT/.baseline_envs/uv_tool/bin/uv" run runner/run_sql_generation.py
"$ROOT/.baseline_envs/uv_tool/bin/uv" run runner/run_sql_revision.py
"$ROOT/.baseline_envs/uv_tool/bin/uv" run runner/run_sql_selection.py
"$ROOT/.baseline_envs/uv_tool/bin/uv" run runner/convert_snapshot_to_sql.py \
  --snapshot_path "$SNAPSHOT" \
  --output "$PRED_JSON" \
  --format json

PYTHONPATH="$ROOT/src" python3 "$ROOT/scripts/evaluate_baseline_predictions.py" \
  --dataset "$DATASET" \
  --split dev \
  --root "$ROOT_DIR" \
  --store "$STORE" \
  --input "$PRED_JSON" \
  --input-format json_map \
  --output "$EVAL_OUT" \
  --condition-id DEEPEYE_SQL_SMOKE \
  --model-label gemma4_26b_sdmc \
  --limit 3
