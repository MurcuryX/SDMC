#!/bin/bash
set -euo pipefail

ROOT="${SDMC_ROOT:-<SERVER1_SDMC_ROOT>}"
PYTHON_BIN="${SDMC_CLIENT_PYTHON:-<SERVER1_DATA_ROOT>/SDMC/envs/macsql-client/bin/python}"
PORT="${SDMC_ENDPOINT_PORT:-18114}"
RUN_ROOT="$ROOT/outputs/rq_final_20260608_023504"
HDC_STORE="<SERVER1_DATA_ROOT>/SDMC_remote_run/local_data/context_stores/tiinsight_hdc_full.sqlite"

cd "$ROOT"

while tmux has-session -t rq3_tisql_spider_schema_sql_queue_p18114_103803 2>/dev/null; do
  sleep 60
done

while pgrep -f "scripts/import_tiinsight_hdc_json.py.*tiinsight_hdc_full.sqlite" >/dev/null 2>&1; do
  sleep 60
done

"$PYTHON_BIN" - <<'PY'
import json
import sqlite3
import sys
from pathlib import Path

spider_dev = Path("<SERVER1_DATA_ROOT>/SDMC_remote_run/local_data/roots/spider/dev.json")
dbs = {row["db_id"] for row in json.load(open(spider_dev, encoding="utf-8"))}
store = Path("<SERVER1_DATA_ROOT>/SDMC_remote_run/local_data/context_stores/tiinsight_hdc_full.sqlite")
conn = sqlite3.connect(store)
try:
    have = {row[0] for row in conn.execute("SELECT database_id FROM hdc_contexts GROUP BY database_id")}
finally:
    conn.close()
missing = sorted(dbs - have)
if missing:
    print({"blocked": "missing_spider_hdc", "missing": missing}, file=sys.stderr)
    sys.exit(4)
print({"hdc_ready_spider_dbs": len(dbs)})
PY

OUT="$RUN_ROOT/rq3_sdmc_spider_llm_gemma4_p${PORT}_$(date +%Y%m%d_%H%M%S)"
export DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-local-calibration}"
export PYTHONPATH="$ROOT/src"
"$PYTHON_BIN" -m sdmc --config "$ROOT/configs/sdmc_gemma4_<gpu-alias>_${PORT}.json" run-experiment \
  --dataset spider \
  --split dev \
  --root <SERVER1_DATA_ROOT>/SDMC_remote_run/local_data/roots/spider \
  --store <SERVER1_DATA_ROOT>/SDMC_remote_run/local_data/context_stores/spider_dev_context_store.sqlite \
  --output "$OUT" \
  --conditions HDC_STYLE \
  --hdc-store "$HDC_STORE" \
  --real-run \
  --allow-api-calls > "$OUT.log" 2>&1
"$PYTHON_BIN" -m sdmc --config "$ROOT/configs/sdmc_gemma4_<gpu-alias>_${PORT}.json" report --kind aggregate --output "$OUT" > "$OUT/aggregate.json" 2>>"$OUT.log"

PYTHONPATH="$ROOT/src" "$PYTHON_BIN" "$ROOT/scripts/prepare_rq3_macsql_spider_inputs.py" \
  --root <SERVER1_DATA_ROOT>/SDMC_remote_run/local_data/roots/spider \
  --store <SERVER1_DATA_ROOT>/SDMC_remote_run/local_data/context_stores/spider_dev_context_store.sqlite \
  --config "$ROOT/configs/sdmc_gemma4_<gpu-alias>_${PORT}.json" \
  --hdc-store "$HDC_STORE" \
  --output-dir "$RUN_ROOT/rq3_inputs" \
  --contexts llm

bash "$ROOT/scripts/baselines/run_macsql_rq3_spider_<gpu-alias>.sh" llm "$PORT" "macsql_rq3_spider_llm_gemma4_p${PORT}_$(date +%Y%m%d_%H%M%S)" > "$RUN_ROOT/rq3_runs/macsql_rq3_llm_queue.log" 2>&1
bash "$ROOT/scripts/baselines/run_tisql_rq3_spider_<gpu-alias>.sh" llm "$PORT" "tisql_rq3_spider_llm_gemma4_p${PORT}_$(date +%Y%m%d_%H%M%S)" > "$RUN_ROOT/rq3_runs/tisql_llm_queue.log" 2>&1
