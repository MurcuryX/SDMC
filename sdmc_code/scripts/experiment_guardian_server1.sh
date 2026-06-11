#!/bin/bash
set -euo pipefail

ROOT="${SDMC_ROOT:-<SERVER1_SDMC_ROOT>}"
RUN_ROOT="${RUN_ROOT:-$ROOT/outputs/rq_final_20260608_023504}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-180}"
STALL_WINDOWS="${STALL_WINDOWS:-2}"
GPU_UTIL_THRESHOLD="${GPU_UTIL_THRESHOLD:-10}"
LOG_DIR="$RUN_ROOT/guardian"
LOG="$LOG_DIR/guardian.log"
STATE="$LOG_DIR/state.tsv"
mkdir -p "$LOG_DIR"
touch "$STATE"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"
}

line_count() {
  local path="$1"
  [ -f "$path" ] && wc -l < "$path" || echo 0
}

state_get() {
  local key="$1"
  awk -F '\t' -v k="$key" '$1 == k {print $0}' "$STATE" | tail -1
}

state_put() {
  local key="$1" count="$2" stable="$3"
  awk -F '\t' -v k="$key" '$1 != k' "$STATE" > "$STATE.tmp" || true
  printf '%s\t%s\t%s\t%s\n' "$key" "$count" "$stable" "$(date +%s)" >> "$STATE.tmp"
  mv "$STATE.tmp" "$STATE"
}

check_target() {
  local name="$1" gpu="$2" expected="$3" file="$4" log_file="${5:-}"
  local count prev_line prev_count prev_stable stable util
  count="$(line_count "$file")"
  prev_line="$(state_get "$name")"
  prev_count="$(printf '%s' "$prev_line" | awk -F '\t' '{print $2}')"
  prev_stable="$(printf '%s' "$prev_line" | awk -F '\t' '{print $3}')"
  [ -n "$prev_stable" ] || prev_stable=0
  stable=0
  if [ -n "$prev_count" ] && [ "$count" = "$prev_count" ] && [ "$count" -lt "$expected" ]; then
    stable=$((prev_stable + 1))
  fi
  util="$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits -i "$gpu" 2>/dev/null | head -1 | tr -d ' ')"
  [ -n "$util" ] || util=0
  state_put "$name" "$count" "$stable"
  log "target=$name gpu=$gpu util=${util}% count=$count/$expected stable_windows=$stable file=$file"
  if [ "$count" -lt "$expected" ] && [ "$stable" -ge "$STALL_WINDOWS" ] && [ "$util" -lt "$GPU_UTIL_THRESHOLD" ]; then
    log "ALERT stall_or_gpu_idle target=$name gpu=$gpu util=${util}% count=$count/$expected stable_windows=$stable"
  fi
  if [ -n "$log_file" ] && [ -f "$log_file" ]; then
    if tail -120 "$log_file" | grep -Eiq 'maximum context length|input_tokens|safe_call_llm error|sleep [0-9]+ seconds|Request .* failed|Traceback|SyntaxError|No such file or directory'; then
      log "ALERT suspicious_log target=$name log=$log_file"
      tail -12 "$log_file" | sed 's/^/[guardian-tail] /' | tee -a "$LOG" >/dev/null
    fi
  fi
}

check_duplicate_rq3() {
  local spider_active
  spider_active="$(ps -ef | grep -E 'MAC-SQL/run.py|tiinsight_repro.run_tisql|sdmc .*run-experiment' | grep -E 'spider|macsql_spider|tiinsight_repro.run_tisql' | grep -v 'rq3_bird' | grep -v grep | wc -l)"
  log "rq3_spider_active_generators=$spider_active"
  if [ "$spider_active" -gt 1 ]; then
    log "ALERT rq3_spider_possible_endpoint_concurrency active_generators=$spider_active"
    ps -ef | grep -E 'MAC-SQL/run.py|tiinsight_repro.run_tisql|sdmc .*run-experiment' | grep -E 'spider|macsql_spider|tiinsight_repro.run_tisql' | grep -v 'rq3_bird' | grep -v grep | sed 's/^/[guardian-proc] /' | tee -a "$LOG" >/dev/null
  fi
  local queue_count util3
  queue_count="$(tmux ls 2>/dev/null | { grep -E 'rq3_.*(queue|restart|resume|fixed)' || true; } | wc -l)"
  util3="$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits -i 3 2>/dev/null | head -1 | tr -d ' ')"
  [ -n "$util3" ] || util3=0
  if [ "$queue_count" -gt 0 ] && [ "$spider_active" -eq 0 ] && [ "$util3" -lt "$GPU_UTIL_THRESHOLD" ]; then
    log "ALERT rq3_queue_present_but_no_active_generator queue_count=$queue_count gpu3_util=${util3}%"
    tmux ls 2>/dev/null | grep -E 'rq3_.*(queue|restart|resume|fixed)' | sed 's/^/[guardian-tmux] /' | tee -a "$LOG" >/dev/null || true
  fi
}

while true; do
  check_target \
    "rq4_bird" 1 10738 \
    "$RUN_ROOT/rq4_bird_gemma4_p18115_full_20260609_101336/predictions.jsonl" \
    "$ROOT/rq_logs/rq4_bird_full_p18115_retry_20260609.log"

  check_target \
    "rq3_macsql_sql" 3 1034 \
    "$RUN_ROOT/rq3_runs/macsql_rq3_spider_sql_compact_gemma4_p18114/predictions_raw.jsonl" \
    "$RUN_ROOT/rq3_runs/macsql_rq3_sql_compact.log"

  check_target \
    "rq3_bird_sdmc_contexts" 1 4602 \
    "$RUN_ROOT/rq3_bird_sdmc_contexts_gemma4_p18115/predictions.jsonl" \
    "$RUN_ROOT/rq3_bird_sdmc_contexts_gemma4_p18115.log"

  check_duplicate_rq3
  sleep "$INTERVAL_SECONDS"
done
