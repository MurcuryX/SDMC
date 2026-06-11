#!/bin/bash
set -euo pipefail

ROOT="${TIINSIGHT_ROOT:-<SERVER1_DATA_ROOT>/TiInsight}"
SDMC_ROOT="${SDMC_ROOT:-<SERVER1_SDMC_ROOT>}"
PYTHON_BIN="${TIINSIGHT_PYTHON:-/data/shared_envs/vllm-0.21-gemma4/bin/python}"
CONTEXT_KIND="${1:-schema}"  # schema | llm | sql
PORT="${2:-18115}"
RUN_ID="${3:-tisql_rq3_bird_${CONTEXT_KIND}_gemma4_p${PORT}_$(date +%Y%m%d_%H%M%S)}"

case "$CONTEXT_KIND" in
  schema)
    HDC_DIR="$SDMC_ROOT/outputs/rq_final_20260608_023504/rq3_tisql_hdc_schema"
    ;;
  llm)
    HDC_DIR="$ROOT/hdc"
    ;;
  sql)
    HDC_DIR="$SDMC_ROOT/outputs/rq_final_20260608_023504/rq3_tisql_hdc_sql"
    ;;
  *)
    echo "Usage: $0 [schema|llm|sql] [port] [run_id]" >&2
    exit 2
    ;;
esac

ENDPOINT="http://127.0.0.1:${PORT}/v1"
if ! curl -fsS "$ENDPOINT/models" >/dev/null; then
  echo "Gemma4 endpoint $ENDPOINT is not reachable." >&2
  exit 3
fi

if [ ! -f "$HDC_DIR/bird/toxicology/hdc.json" ]; then
  echo "HDC directory is not ready for context=${CONTEXT_KIND}: $HDC_DIR" >&2
  exit 4
fi

cd "$ROOT"
export TIINSIGHT_ROOT="$ROOT"
export TIINSIGHT_DATA_DIR="$ROOT/data"
export TIINSIGHT_HDC_DIR="$HDC_DIR"
export TIINSIGHT_RUNS_DIR="$SDMC_ROOT/outputs/rq_final_20260608_023504/rq3_runs/tisql"
export OPENAI_BASE_URL="$ENDPOINT"
export OPENAI_API_KEY="${OPENAI_API_KEY:-local-calibration}"
export TIINSIGHT_MODEL="${TIINSIGHT_MODEL:-gemma4_26b_sdmc}"
export TIINSIGHT_TEMPERATURE=0
export TIINSIGHT_MAX_TOKENS="${TIINSIGHT_MAX_TOKENS:-1536}"
export PYTHONPATH="$ROOT/repro"
mkdir -p "$TIINSIGHT_RUNS_DIR"

"$PYTHON_BIN" -m tiinsight_repro.run_tisql \
  --dataset bird \
  --split dev \
  --run-name "$RUN_ID" \
  --strategy v2 \
  --max-refine 2

"$PYTHON_BIN" -m tiinsight_repro.evaluate \
  --dataset bird \
  --split dev \
  --run-name "$RUN_ID" \
  --output-name eval.json \
  --timeout-seconds 30

echo "$TIINSIGHT_RUNS_DIR/$RUN_ID"
