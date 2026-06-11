#!/bin/bash
set -euo pipefail

ROOT="${SDMC_ROOT:-<SERVER1_SDMC_ROOT>}"
LOG_DIR="$ROOT/rq_logs"
mkdir -p "$LOG_DIR"

has_session() {
  tmux has-session -t "$1" >/dev/null 2>&1
}

launch_once() {
  local session="$1"
  local cmd="$2"
  local log="$3"
  if has_session "$session"; then
    return 0
  fi
  tmux new-session -d -s "$session" "bash -lc '$cmd > $log 2>&1'"
}

wait_for_session_done() {
  local session="$1"
  while has_session "$session"; do
    sleep 60
  done
}

cd "$ROOT"

# Spider lane: RQ2 full -> RQ4 full.
(
  wait_for_session_done rq2_spider_full_p18114_20260609
  launch_once \
    rq4_spider_full_p18114_20260609 \
    "cd $ROOT && SDMC_ENDPOINT_PORT=18114 bash scripts/run_rq4_gemma4_<gpu-alias>.sh full spider" \
    "$LOG_DIR/rq4_spider_full_p18114_20260609.log"
) &

# BIRD lane: RQ2 full -> RQ4 full.
(
  wait_for_session_done rq2_bird_full_p18115_20260609
  launch_once \
    rq4_bird_full_p18115_20260609 \
    "cd $ROOT && SDMC_ENDPOINT_PORT=18115 bash scripts/run_rq4_gemma4_<gpu-alias>.sh full bird" \
    "$LOG_DIR/rq4_bird_full_p18115_20260609.log"
) &

wait
