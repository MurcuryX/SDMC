#!/usr/bin/env bash
set -euo pipefail

cd "$HOME/Backup/SDMC"
export PYTHONPATH=src

OUT_ROOT="${OUT_ROOT:-outputs/context_build_v2}"
LOG_DIR="outputs/logs/stage_a_full_v2_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$LOG_DIR"

run_split() {
  local dataset="$1"
  local split="$2"
  local root="$3"
  local out="${OUT_ROOT}/${dataset}/${split}"
  local log="$LOG_DIR/${dataset}_${split}.log"

  {
    echo "===== $(date '+%F %T') START ${dataset}/${split} ====="
    echo "root=$root"
    echo "output=$out"
    /usr/bin/time -p env PYTHONPATH=src python3 -m sdmc inventory \
      --dataset "$dataset" \
      --split "$split" \
      --root "$root" \
      --output "$out"
    /usr/bin/time -p env PYTHONPATH=src python3 -m sdmc build \
      --dataset "$dataset" \
      --split "$split" \
      --root "$root" \
      --output "$out" \
      --materialize-graph \
      --force
    echo "===== $(date '+%F %T') DONE ${dataset}/${split} ====="
  } 2>&1 | tee "$log"
}

SPIDER_ROOT="$HOME/Backup/share_data/text_to_sql/spider_full/extracted/spider_data"
BIRD_ROOT="$HOME/Backup/share_data/text_to_sql/bird_full"

echo "Stage A full v2 run started at $(date '+%F %T')"
echo "Output root: $OUT_ROOT"
echo "Logs: $LOG_DIR"

run_split spider dev "$SPIDER_ROOT"
run_split spider train "$SPIDER_ROOT"
run_split spider test "$SPIDER_ROOT"
run_split bird dev "$BIRD_ROOT"
run_split bird train "$BIRD_ROOT"

echo "Stage A full v2 run finished at $(date '+%F %T')"
