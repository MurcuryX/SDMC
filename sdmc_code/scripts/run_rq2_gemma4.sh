#!/bin/bash
set -euo pipefail

ROOT="<SDMC_ROOT>"
if [ "${SDMC_GEMMA4_LOCKED:-0}" != "1" ]; then
  export SDMC_GEMMA4_LOCKED=1
  exec "$ROOT/scripts/baselines/with_gemma4_endpoint_lock.sh" "$0" "$@"
fi
MODE="${1:-smoke}"      # smoke | full
DATASET="${2:-spider}"  # spider | bird
CONDITIONS="RAW_SCHEMA,SDMC_FULL,SDMC_FLAT_STORE,SDMC_GRAPH_SCHEMA_ONLY,SDMC_GRAPH_NO_REL,SDMC"
CONFIG="$ROOT/configs/sdmc_gemma4_local_verified.yaml"
RUN_ROOT="$ROOT/outputs/rq_final_20260608_023504"

case "$DATASET" in
  spider)
    DATA_ROOT="$RUN_ROOT/local_data/roots/spider"
    STORE="$RUN_ROOT/local_data/context_stores/spider_dev_context_store.sqlite"
    ;;
  bird)
    DATA_ROOT="$RUN_ROOT/local_data/roots/bird"
    STORE="$RUN_ROOT/local_data/context_stores/bird_dev_context_store.sqlite"
    ;;
  *)
    echo "Usage: $0 [smoke|full] [spider|bird]" >&2
    exit 2
    ;;
esac

case "$MODE" in
  smoke)
    LIMIT_ARGS=(--limit 20)
    OUT="$RUN_ROOT/rq2_${DATASET}_gemma4_smoke_$(date +%Y%m%d_%H%M%S)"
    ;;
  full)
    LIMIT_ARGS=()
    OUT="$RUN_ROOT/rq2_${DATASET}_gemma4_full_$(date +%Y%m%d_%H%M%S)"
    ;;
  *)
    echo "Usage: $0 [smoke|full] [spider|bird]" >&2
    exit 2
    ;;
esac

if ! curl -fsS http://127.0.0.1:18114/v1/models >/dev/null; then
  echo "Gemma4 endpoint http://127.0.0.1:18114/v1 is not reachable." >&2
  exit 3
fi

cd "$ROOT"
export DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-local-calibration}"
export PYTHONPATH="$ROOT/src"

python3 -m sdmc --config "$CONFIG" run-experiment \
  --dataset "$DATASET" \
  --split dev \
  --root "$DATA_ROOT" \
  --store "$STORE" \
  --output "$OUT" \
  --conditions "$CONDITIONS" \
  "${LIMIT_ARGS[@]}" \
  --real-run \
  --allow-api-calls

python3 -m sdmc --config "$CONFIG" report --kind aggregate --output "$OUT" > "$OUT/aggregate.json"
python3 -m sdmc --config "$CONFIG" report --kind paired --output "$OUT" --baseline RAW_SCHEMA --ours SDMC > "$OUT/paired_raw_vs_sdmc.json"
python3 -m sdmc --config "$CONFIG" report --kind paired --output "$OUT" --baseline SDMC_FLAT_STORE --ours SDMC > "$OUT/paired_flat_vs_sdmc.json"

echo "$OUT"
