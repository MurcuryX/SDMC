#!/bin/bash
set -euo pipefail

ROOT="${SDMC_ROOT:-<SERVER1_SDMC_ROOT>}"
DATA_ROOT="${SDMC_DATA_ROOT:-<SERVER1_DATA_ROOT>/SDMC_remote_run/local_data}"
PYTHON_BIN="${SDMC_CLIENT_PYTHON:-<SERVER1_DATA_ROOT>/SDMC/envs/macsql-client/bin/python}"
PORT="${SDMC_ENDPOINT_PORT:-18115}"
MODE="${1:-smoke}"
DATASET="${2:-bird}"
CONDITIONS="SDMC_NO_COLUMN_CTX,SDMC_NO_TABLE_CTX,SDMC_NO_DATABASE_CTX,SDMC_ONLY_COLUMN_CTX,SDMC_ONLY_TABLE_CTX,SDMC_ONLY_DATABASE_CTX,SDMC"
RUN_ROOT="$ROOT/outputs/rq_final_20260608_023504"
CONFIG="$ROOT/configs/sdmc_gemma4_<gpu-alias>_${PORT}.json"

case "$DATASET" in
  spider)
    ROOT_DIR="$DATA_ROOT/roots/spider"
    STORE="$DATA_ROOT/context_stores/spider_dev_context_store.sqlite"
    ;;
  bird)
    ROOT_DIR="$DATA_ROOT/roots/bird"
    STORE="$DATA_ROOT/context_stores/bird_dev_context_store.sqlite"
    ;;
  *)
    echo "Usage: $0 [smoke|full] [spider|bird]" >&2
    exit 2
    ;;
esac

case "$MODE" in
  smoke)
    LIMIT_ARGS=(--limit 20)
    OUT="$RUN_ROOT/rq4_${DATASET}_gemma4_p${PORT}_smoke_$(date +%Y%m%d_%H%M%S)"
    ;;
  full)
    LIMIT_ARGS=()
    OUT="$RUN_ROOT/rq4_${DATASET}_gemma4_p${PORT}_full_$(date +%Y%m%d_%H%M%S)"
    ;;
  *)
    echo "Usage: $0 [smoke|full] [spider|bird]" >&2
    exit 2
    ;;
esac

if ! curl -fsS "http://127.0.0.1:${PORT}/v1/models" >/dev/null; then
  echo "Gemma4 endpoint http://127.0.0.1:${PORT}/v1 is not reachable." >&2
  exit 3
fi

cd "$ROOT"
export DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-local-calibration}"
export PYTHONPATH="$ROOT/src"

"$PYTHON_BIN" -m sdmc --config "$CONFIG" run-experiment \
  --dataset "$DATASET" \
  --split dev \
  --root "$ROOT_DIR" \
  --store "$STORE" \
  --output "$OUT" \
  --conditions "$CONDITIONS" \
  "${LIMIT_ARGS[@]}" \
  --real-run \
  --allow-api-calls

"$PYTHON_BIN" -m sdmc --config "$CONFIG" report --kind aggregate --output "$OUT" > "$OUT/aggregate.json"
"$PYTHON_BIN" -m sdmc --config "$CONFIG" report --kind paired --output "$OUT" --baseline SDMC_NO_COLUMN_CTX --ours SDMC > "$OUT/paired_no_column_vs_sdmc.json"
"$PYTHON_BIN" -m sdmc --config "$CONFIG" report --kind paired --output "$OUT" --baseline SDMC_NO_TABLE_CTX --ours SDMC > "$OUT/paired_no_table_vs_sdmc.json"
"$PYTHON_BIN" -m sdmc --config "$CONFIG" report --kind paired --output "$OUT" --baseline SDMC_NO_DATABASE_CTX --ours SDMC > "$OUT/paired_no_database_vs_sdmc.json"

echo "$OUT"
