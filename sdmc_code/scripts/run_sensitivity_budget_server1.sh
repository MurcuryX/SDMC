#!/bin/bash
set -euo pipefail

ROOT="${SDMC_ROOT:-<SERVER1_SDMC_ROOT>}"
DATA_ROOT="${SDMC_DATA_ROOT:-<SERVER1_DATA_ROOT>/SDMC_remote_run/local_data}"
PYTHON_BIN="${SDMC_CLIENT_PYTHON:-<SERVER1_DATA_ROOT>/SDMC/envs/macsql-client/bin/python}"
RUN_ROOT="$ROOT/outputs/rq_final_20260608_023504"
LOG_DIR="$ROOT/rq_logs"
MODEL_PATH="${SDMC_GEMMA4_MODEL_PATH:-$HOME/Backup/share_model/huggingface/hub/models--google--gemma-4-26B-A4B-it/snapshots/462a98a12e28e2cbcfccaf78fe41e3e50235e6ae}"
VLLM_ENV="${SDMC_VLLM_ENV:-/data/shared_envs/vllm-0.21-gemma4}"

mkdir -p "$LOG_DIR" "$RUN_ROOT/sensitivity_budget/configs"

usage() {
  echo "Usage: $0 launch | run-lane <spider|bird> <port> | run-one <spider|bird> <budget> <port>" >&2
}

gpu_is_free() {
  local gpu="$1"
  local used
  used="$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i "$gpu" 2>/dev/null | tr -d ' ')"
  [ -n "$used" ] && [ "$used" -lt 2048 ]
}

ensure_endpoint() {
  local gpu="$1" port="$2" label="gemma4_26b_sdmc"
  if curl -fsS "http://127.0.0.1:${port}/v1/models" >/dev/null 2>&1; then
    echo "endpoint_ready_existing port=${port}"
    return 0
  fi
  if ! gpu_is_free "$gpu"; then
    echo "GPU ${gpu} is not free; refusing to start endpoint ${port}." >&2
    return 3
  fi
  local session="sdmc_vllm_sensitivity_${port}"
  if ! tmux has-session -t "$session" >/dev/null 2>&1; then
    tmux new-session -d -s "$session" \
      "bash -lc 'source ~/miniforge3/etc/profile.d/conda.sh 2>/dev/null || true; conda activate $VLLM_ENV; CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=$gpu python -m vllm.entrypoints.openai.api_server --model $MODEL_PATH --served-model-name $label --host 127.0.0.1 --port $port --trust-remote-code --tensor-parallel-size 1 --max-model-len 8192 --max-num-batched-tokens 8192 --gpu-memory-utilization 0.90 > $LOG_DIR/vllm_sensitivity_${port}.log 2>&1'"
  fi
  for _ in $(seq 1 120); do
    if curl -fsS "http://127.0.0.1:${port}/v1/models" >/dev/null 2>&1; then
      echo "endpoint_ready_started port=${port} gpu=${gpu}"
      return 0
    fi
    sleep 10
  done
  echo "Endpoint ${port} did not become ready. See $LOG_DIR/vllm_sensitivity_${port}.log" >&2
  return 4
}

make_config() {
  local budget="$1" port="$2" out="$RUN_ROOT/sensitivity_budget/configs/sdmc_gemma4_${budget}_p${port}.json"
  "$PYTHON_BIN" - "$ROOT/configs/sdmc_gemma4_<gpu-alias>_${port}.json" "$out" "$budget" "$port" <<'PY'
import json
import sys
from pathlib import Path

base = Path(sys.argv[1])
out = Path(sys.argv[2])
budget = sys.argv[3]
port = sys.argv[4]
data = json.loads(base.read_text())

settings = {
    "small": {
        "max_selected_tables": 8,
        "max_selected_columns": 80,
        "max_value_encoding_nodes": 20,
        "max_statistic_nodes": 40,
        "max_relationship_edges": 40,
        "max_context_items": 80,
    },
    "default": {
        "max_selected_tables": 12,
        "max_selected_columns": 120,
        "max_value_encoding_nodes": 40,
        "max_statistic_nodes": 80,
        "max_relationship_edges": 80,
        "max_context_items": 160,
    },
    "large": {
        "max_selected_tables": 16,
        "max_selected_columns": 160,
        "max_value_encoding_nodes": 60,
        "max_statistic_nodes": 120,
        "max_relationship_edges": 120,
        "max_context_items": 240,
    },
    "verylarge": {
        "max_selected_tables": 20,
        "max_selected_columns": 220,
        "max_value_encoding_nodes": 80,
        "max_statistic_nodes": 160,
        "max_relationship_edges": 160,
        "max_context_items": 320,
    },
}
if budget not in settings:
    raise SystemExit(f"unknown budget: {budget}")
data["stage_b"].update(settings[budget])
data["stage_b"]["endpoint"] = f"http://127.0.0.1:{port}/v1"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(data, indent=2), encoding="utf-8")
print(out)
PY
}

dataset_paths() {
  local dataset="$1"
  case "$dataset" in
    spider)
      echo "$DATA_ROOT/roots/spider $DATA_ROOT/context_stores/spider_dev_context_store.sqlite"
      ;;
    bird)
      echo "$DATA_ROOT/roots/bird $DATA_ROOT/context_stores/bird_dev_context_store.sqlite"
      ;;
    *)
      usage
      exit 2
      ;;
  esac
}

run_one() {
  local dataset="$1" budget="$2" port="$3"
  read -r root_dir store <<<"$(dataset_paths "$dataset")"
  local config out ts
  config="$(make_config "$budget" "$port")"
  ts="$(date +%Y%m%d_%H%M%S)"
  out="$RUN_ROOT/sensitivity_budget/${dataset}_${budget}_gemma4_p${port}_${ts}"
  mkdir -p "$out"
  {
    echo "dataset=$dataset"
    echo "budget=$budget"
    echo "port=$port"
    echo "config=$config"
    echo "root=$root_dir"
    echo "store=$store"
    date
  } > "$out/run_meta.txt"
  cd "$ROOT"
  export DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-local-calibration}"
  export PYTHONPATH="$ROOT/src"
  "$PYTHON_BIN" -m sdmc --config "$config" run-experiment \
    --dataset "$dataset" \
    --split dev \
    --root "$root_dir" \
    --store "$store" \
    --output "$out" \
    --conditions SDMC \
    --real-run \
    --allow-api-calls
  "$PYTHON_BIN" -m sdmc --config "$config" report --kind aggregate --output "$out" > "$out/aggregate.json"
  echo "$out"
}

run_lane() {
  local dataset="$1" port="$2"
  for budget in small default large verylarge; do
    run_one "$dataset" "$budget" "$port"
  done
}

launch() {
  ensure_endpoint "${SDMC_SPIDER_GPU:-2}" 18114
  ensure_endpoint "${SDMC_BIRD_GPU:-3}" 18115
  if ! tmux has-session -t sdmc_sensitivity_spider_p18114 >/dev/null 2>&1; then
    tmux new-session -d -s sdmc_sensitivity_spider_p18114 \
      "bash -lc 'cd $ROOT && bash scripts/run_sensitivity_budget_<gpu-alias>.sh run-lane spider 18114 > $LOG_DIR/sensitivity_spider_p18114.log 2>&1'"
  fi
  if ! tmux has-session -t sdmc_sensitivity_bird_p18115 >/dev/null 2>&1; then
    tmux new-session -d -s sdmc_sensitivity_bird_p18115 \
      "bash -lc 'cd $ROOT && bash scripts/run_sensitivity_budget_<gpu-alias>.sh run-lane bird 18115 > $LOG_DIR/sensitivity_bird_p18115.log 2>&1'"
  fi
  echo "launched sensitivity lanes: spider->18114/GPU${SDMC_SPIDER_GPU:-2}, bird->18115/GPU${SDMC_BIRD_GPU:-3}"
}

cmd="${1:-}"
case "$cmd" in
  launch)
    launch
    ;;
  run-lane)
    [ "$#" -eq 3 ] || { usage; exit 2; }
    run_lane "$2" "$3"
    ;;
  run-one)
    [ "$#" -eq 4 ] || { usage; exit 2; }
    run_one "$2" "$3" "$4"
    ;;
  *)
    usage
    exit 2
    ;;
esac
