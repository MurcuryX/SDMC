#!/bin/bash
set -euo pipefail

ROOT="${SDMC_ROOT:-<SERVER1_SDMC_ROOT>}"
RUN_ROOT="$ROOT/outputs/rq_final_20260608_023504"
LOG="$RUN_ROOT/guardian/rq3_bird_guardian.log"
mkdir -p "$(dirname "$LOG")"

count_lines() {
  local path="$1"
  [ -f "$path" ] && wc -l < "$path" || echo 0
}

while true; do
  ts="$(date '+%F %T')"
  gpu="$(nvidia-smi --query-gpu=index,utilization.gpu --format=csv,noheader,nounits | tr '\n' ';')"
  mac_schema="$(count_lines "$RUN_ROOT/rq3_runs/macsql_rq3_bird_schema_gemma4_p18114_20260609_1638/predictions_raw.jsonl")"
  mac_llm="$(count_lines "$RUN_ROOT/rq3_runs/macsql_rq3_bird_llm_gemma4_p18114_20260609_1638/predictions_raw.jsonl")"
  mac_sql="$(count_lines "$RUN_ROOT/rq3_runs/macsql_rq3_bird_sql_gemma4_p18114_20260609_1638/predictions_raw.jsonl")"
  tis_schema="$(count_lines "$RUN_ROOT/rq3_runs/tisql/tisql_rq3_bird_schema_gemma4_p18115_20260609_1635/predictions.jsonl")"
  tis_llm="$(count_lines "$RUN_ROOT/rq3_runs/tisql/tisql_rq3_bird_llm_gemma4_p18115_20260609_1635/predictions.jsonl")"
  tis_sql="$(count_lines "$RUN_ROOT/rq3_runs/tisql/tisql_rq3_bird_sql_gemma4_p18115_20260609_1635/predictions.jsonl")"
  sql_input="missing"
  [ -f "$RUN_ROOT/rq3_inputs_bird/macsql_bird_rq3_sql.json" ] && sql_input="ready"
  printf '[%s] gpu=%s mac=schema:%s,llm:%s,sql:%s tisql=schema:%s,llm:%s,sql:%s mac_sql_input=%s\n' \
    "$ts" "$gpu" "$mac_schema" "$mac_llm" "$mac_sql" "$tis_schema" "$tis_llm" "$tis_sql" "$sql_input" >> "$LOG"
  sleep "${INTERVAL_SECONDS:-120}"
done
