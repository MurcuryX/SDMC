#!/usr/bin/env bash
set -euo pipefail

BASE="<SDMC_ROOT>"
cd "$BASE"

INTERVAL_SECONDS="${INTERVAL_SECONDS:-1200}"
RUN_ID="${RUN_ID:-rq_final_$(date '+%Y%m%d_%H%M%S')}"
RUN_ROOT="${RUN_ROOT:-$BASE/outputs/$RUN_ID}"
LOCAL_ROOT="$RUN_ROOT/local_data"
LOG_DIR="$RUN_ROOT/logs"
LOG="$LOG_DIR/monitor.log"
API_KEY_FILE="$BASE/<API_KEY_FILE>"
CONFIG_PRO="$BASE/configs/sdmc_deepseek_v4pro_verified.yaml"
CONFIG_FLASH="$BASE/configs/sdmc_deepseek_v4_flash_verified.yaml"
CONFIG_LOCAL="$BASE/configs/sdmc_deepseek_v4pro_verified.yaml"

SPIDER_STORE="$LOCAL_ROOT/context_stores/spider_dev_context_store.sqlite"
BIRD_STORE="$LOCAL_ROOT/context_stores/bird_dev_context_store.sqlite"
SPIDER_ROOT="$LOCAL_ROOT/roots/spider"
BIRD_ROOT="$LOCAL_ROOT/roots/bird"
HDC_SPIDER="$BASE/outputs/current_experiments/local_data/context_stores/tiinsight_hdc_qwen25_spider.sqlite"
HDC_BIRD="$BASE/outputs/current_experiments/local_data/context_stores/tiinsight_hdc_qwen25_bird.sqlite"

mkdir -p "$LOCAL_ROOT/context_stores" "$SPIDER_ROOT/database" "$BIRD_ROOT/dev_databases" "$LOG_DIR"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"
}

mark_done() {
  mkdir -p "$RUN_ROOT/.done"
  touch "$RUN_ROOT/.done/$1"
}

is_done() {
  [ -f "$RUN_ROOT/.done/$1" ]
}

ssh_ready() {
  ssh -o BatchMode=yes -o ConnectTimeout=8 <gpu-alias> 'hostname >/dev/null'
}

run_step() {
  local name="$1"
  shift
  if is_done "$name"; then
    log "skip completed step: $name"
    return 0
  fi
  log "start step: $name"
  "$@" 2>&1 | tee -a "$LOG_DIR/${name}.log"
  mark_done "$name"
  log "done step: $name"
}

<gpu-alias>_preflight() {
  ssh <gpu-alias> 'set -e
    echo "host=$(hostname)"
    echo "date=$(date "+%Y-%m-%d %H:%M:%S %Z")"
    echo "home=$HOME"
    echo "backup=$(cd <SERVER1_DATA_ROOT> && pwd)"
    echo "=== GPUs ==="
    nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits || true
    echo "=== SDMC stores ==="
    python3 - <<'"'"'PY'"'"'
from pathlib import Path
import sqlite3
for p in [
    Path("<SERVER1_DATA_ROOT>/SDMC/outputs/context_build_v2/spider/dev/context_store.sqlite").expanduser(),
    Path("<SERVER1_DATA_ROOT>/SDMC/outputs/context_build_v2/bird/dev/context_store.sqlite").expanduser(),
]:
    print(p)
    if not p.exists():
        print({"exists": False})
        continue
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    total = conn.execute("SELECT COUNT(*) n FROM databases").fetchone()["n"]
    complete = conn.execute("SELECT COUNT(*) n FROM databases WHERE build_status IN (?, ?)", ("context_complete", "graph_complete")).fetchone()["n"]
    graph = conn.execute("SELECT COUNT(*) n FROM dataset_graph_summary WHERE build_status=?", ("graph_complete",)).fetchone()["n"]
    failed = conn.execute("SELECT COUNT(*) n FROM context_items WHERE execution_status=?", ("failed",)).fetchone()["n"]
    print({"exists": True, "databases": total, "complete": complete, "graph_complete": graph, "failed_context": failed})
    conn.close()
PY'
}

scan_models() {
  ssh <gpu-alias> 'set -e
    HUB=<SERVER1_DATA_ROOT>/share_model/huggingface/hub
    echo "hub=$HUB"
    find "$HUB" -maxdepth 1 -type d -name "models--*" | grep -Ei "qwen|gemma|llama|deepseek" | sort || true
  ' | tee "$RUN_ROOT/model_candidates.txt"
}

copy_dev_resources() {
  log "copy context stores and dev data from <gpu-alias>; no train/checkpoint/shared-model writes."
  rsync -az <gpu-alias>:<SERVER1_DATA_ROOT>/SDMC/outputs/context_build_v2/spider/dev/context_store.sqlite "$SPIDER_STORE"
  rsync -az <gpu-alias>:<SERVER1_DATA_ROOT>/SDMC/outputs/context_build_v2/bird/dev/context_store.sqlite "$BIRD_STORE"

  rsync -az <gpu-alias>:<SERVER1_DATA_ROOT>/share_data/text_to_sql/spider_full/extracted/spider_data/dev.json "$SPIDER_ROOT/dev.json"
  rsync -az <gpu-alias>:<SERVER1_DATA_ROOT>/share_data/text_to_sql/spider_full/extracted/spider_data/tables.json "$SPIDER_ROOT/tables.json"
  python3 - <<PY > "$RUN_ROOT/spider_dev_dbs.txt"
import json
from pathlib import Path
records = json.loads(Path("$SPIDER_ROOT/dev.json").read_text(encoding="utf-8"))
for db in sorted({r["db_id"] for r in records}):
    print(db)
PY
  while read -r db; do
    [ -n "$db" ] || continue
    rsync -az "<gpu-alias>:<SERVER1_DATA_ROOT>/share_data/text_to_sql/spider_full/extracted/spider_data/database/$db" "$SPIDER_ROOT/database/"
  done < "$RUN_ROOT/spider_dev_dbs.txt"

  rsync -az <gpu-alias>:<SERVER1_DATA_ROOT>/share_data/text_to_sql/bird_full/extracted/dev_20240627/dev.json "$BIRD_ROOT/dev.json"
  rsync -az <gpu-alias>:<SERVER1_DATA_ROOT>/share_data/text_to_sql/bird_full/extracted/dev_20240627/dev_tables.json "$BIRD_ROOT/dev_tables.json"
  rsync -az <gpu-alias>:<SERVER1_DATA_ROOT>/share_data/text_to_sql/bird_full/extracted/dev_20240627/dev_databases/dev_databases/ "$BIRD_ROOT/dev_databases/"
}

patch_store_paths() {
  python3 - <<PY
from pathlib import Path
import json
import sqlite3

jobs = [
    (Path("$SPIDER_STORE"), json.loads(Path("$SPIDER_ROOT/dev.json").read_text(encoding="utf-8")), lambda db: Path("$SPIDER_ROOT") / "database" / db / f"{db}.sqlite"),
    (Path("$BIRD_STORE"), json.loads(Path("$BIRD_ROOT/dev.json").read_text(encoding="utf-8")), lambda db: Path("$BIRD_ROOT") / "dev_databases" / db / f"{db}.sqlite"),
]
for store, records, path_fn in jobs:
    dbs = sorted({r.get("db_id") or r.get("database_id") for r in records})
    conn = sqlite3.connect(store)
    missing = []
    for db in dbs:
        p = path_fn(db)
        if not p.exists():
            missing.append((db, str(p)))
            continue
        conn.execute("UPDATE databases SET sqlite_path=? WHERE database_id=?", (str(p), db))
    conn.commit()
    conn.close()
    print({"store": str(store), "patched_dbs": len(dbs), "missing": len(missing)})
    if missing:
        raise SystemExit(f"missing sqlite files: {missing[:10]}")
PY
}

dry_run_prompts() {
  local condition_set="$1"
  local suffix="$2"
  PYTHONPATH=src python3 -m sdmc --config "$CONFIG_LOCAL" run-experiment \
    --dataset spider --split dev --root "$SPIDER_ROOT" --store "$SPIDER_STORE" \
    --output "$RUN_ROOT/dry_spider_${suffix}" \
    --conditions "$condition_set" \
    --hdc-store "$HDC_SPIDER"
  PYTHONPATH=src python3 -m sdmc --config "$CONFIG_LOCAL" run-experiment \
    --dataset bird --split dev --root "$BIRD_ROOT" --store "$BIRD_STORE" \
    --output "$RUN_ROOT/dry_bird_${suffix}" \
    --conditions "$condition_set" \
    --hdc-store "$HDC_BIRD"
}

run_deepseek_model() {
  local label="$1"
  local config="$2"
  local conditions="$3"
  local suffix="$4"
  PYTHONPATH=src python3 -m sdmc --config "$config" run-experiment \
    --dataset spider --split dev --root "$SPIDER_ROOT" --store "$SPIDER_STORE" \
    --output "$RUN_ROOT/${suffix}_${label}_spider" \
    --conditions "$conditions" \
    --api-key-file "$API_KEY_FILE" \
    --hdc-store "$HDC_SPIDER" \
    --real-run --allow-api-calls
  PYTHONPATH=src python3 -m sdmc --config "$config" report --kind aggregate --output "$RUN_ROOT/${suffix}_${label}_spider" > "$RUN_ROOT/${suffix}_${label}_spider_aggregate.json"

  PYTHONPATH=src python3 -m sdmc --config "$config" run-experiment \
    --dataset bird --split dev --root "$BIRD_ROOT" --store "$BIRD_STORE" \
    --output "$RUN_ROOT/${suffix}_${label}_bird" \
    --conditions "$conditions" \
    --api-key-file "$API_KEY_FILE" \
    --hdc-store "$HDC_BIRD" \
    --real-run --allow-api-calls
  PYTHONPATH=src python3 -m sdmc --config "$config" report --kind aggregate --output "$RUN_ROOT/${suffix}_${label}_bird" > "$RUN_ROOT/${suffix}_${label}_bird_aggregate.json"
}

remote_resolve_model() {
  local grep_pattern="$1"
  ssh <gpu-alias> "python3 - <<'PY'
from pathlib import Path
import re
pat = re.compile(r'''$grep_pattern''', re.I)
hub = Path('<SERVER1_DATA_ROOT>/share_model/huggingface/hub').expanduser()
matches = []
for d in hub.glob('models--*'):
    if pat.search(d.name):
        snaps = sorted((d / 'snapshots').glob('*'), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        if snaps:
            matches.append(str(snaps[0]))
if matches:
    print(matches[0])
PY"
}

choose_gpu() {
  ssh <gpu-alias> "python3 - <<'PY'
import subprocess
out = subprocess.check_output([
    'nvidia-smi',
    '--query-gpu=index,name,memory.used,memory.total',
    '--format=csv,noheader,nounits',
], text=True)
candidates = []
for line in out.strip().splitlines():
    idx, name, used, total = [x.strip() for x in line.split(',')]
    used_i, total_i = int(used), int(total)
    if 'A100' in name and used_i < 5000:
        candidates.append((0, used_i, idx))
    elif used_i < 3000:
        candidates.append((1, used_i, idx))
if candidates:
    print(sorted(candidates)[0][2])
PY"
}

port_open() {
  local port="$1"
  python3 - <<PY
import socket, sys
s = socket.socket()
s.settimeout(1)
try:
    s.connect(("127.0.0.1", int("$port")))
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
}

start_local_model_server() {
  local label="$1"
  local pattern="$2"
  local remote_port="$3"
  local local_port="$4"
  local model_path
  model_path="$(remote_resolve_model "$pattern" | tail -1)"
  if [ -z "$model_path" ]; then
    log "model $label not found on <gpu-alias>; skip"
    return 2
  fi
  local gpu
  gpu="$(choose_gpu | tail -1)"
  if [ -z "$gpu" ]; then
    log "no suitable idle GPU for $label; skip"
    return 2
  fi
  log "starting $label on <gpu-alias> gpu=$gpu model=$model_path remote_port=$remote_port local_port=$local_port"
  ssh <gpu-alias> "mkdir -p <SERVER1_DATA_ROOT>/SDMC/rq_logs && tmux new-session -d -s sdmc_vllm_${label}_${remote_port} 'bash -lc \"source ~/miniforge3/etc/profile.d/conda.sh 2>/dev/null || source /data/shared_envs/vllm-0.21-gemma4/etc/profile.d/conda.sh 2>/dev/null || true; conda activate /data/shared_envs/vllm-0.21-gemma4; CUDA_VISIBLE_DEVICES=$gpu python -m vllm.entrypoints.openai.api_server --model $model_path --served-model-name $label --host 127.0.0.1 --port $remote_port --trust-remote-code --tensor-parallel-size 1 > <SERVER1_DATA_ROOT>/SDMC/rq_logs/vllm_${label}_${remote_port}.log 2>&1\"' || true"
  tmux new-session -d -s "sdmc_tunnel_${label}_${local_port}" "ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:${local_port}:127.0.0.1:${remote_port} <gpu-alias>" 2>/dev/null || true
  for _ in $(seq 1 120); do
    if port_open "$local_port"; then
      log "$label endpoint ready on 127.0.0.1:$local_port"
      return 0
    fi
    sleep 10
  done
  log "$label endpoint not ready after wait; skip"
  return 2
}

run_local_model() {
  local label="$1"
  local local_port="$2"
  local conditions="$3"
  local dry_suffix="$4"
  local out_suffix="$5"
  PYTHONPATH=src python3 scripts/run_local_model_calibration.py \
    --dataset spider --split dev --root "$SPIDER_ROOT" --store "$SPIDER_STORE" \
    --dry-output "$RUN_ROOT/dry_spider_${dry_suffix}" \
    --output "$RUN_ROOT/${out_suffix}_${label}_spider" \
    --endpoint "http://127.0.0.1:${local_port}/v1" \
    --model "$label" --model-label "$label" \
    --conditions "$conditions" --sample 20000 --seed 13 \
    --max-output-tokens 768 --temperature 0 --timeout 240 \
    --enable-explain-repair --enable-runtime-repair --max-repair-attempts 2
  PYTHONPATH=src python3 scripts/run_local_model_calibration.py \
    --dataset bird --split dev --root "$BIRD_ROOT" --store "$BIRD_STORE" \
    --dry-output "$RUN_ROOT/dry_bird_${dry_suffix}" \
    --output "$RUN_ROOT/${out_suffix}_${label}_bird" \
    --endpoint "http://127.0.0.1:${local_port}/v1" \
    --model "$label" --model-label "$label" \
    --conditions "$conditions" --sample 20000 --seed 13 \
    --max-output-tokens 768 --temperature 0 --timeout 240 \
    --enable-explain-repair --enable-runtime-repair --max-repair-attempts 2
}

run_table2() {
  dry_run_prompts "SDMC" "sdmc"
  run_deepseek_model "deepseek_v4pro" "$CONFIG_PRO" "SDMC" "rq1_table2"
  run_deepseek_model "deepseek_v4flash" "$CONFIG_FLASH" "SDMC" "rq1_table2"

  if start_local_model_server "gemma4_26b_sdmc" "gemma.*(4|26b)|gemma.*26" 18114 18114; then
    run_local_model "gemma4_26b_sdmc" 18114 "SDMC" "sdmc" "rq1_table2"
  fi
  if start_local_model_server "qwen25_14b_sdmc" "qwen.*2.?5.*14" 18125 18125; then
    run_local_model "qwen25_14b_sdmc" 18125 "SDMC" "sdmc" "rq1_table2"
  fi
  if start_local_model_server "llama3_8b_sdmc" "llama.*3.*8b|meta.*llama.*8b" 18138 18138; then
    run_local_model "llama3_8b_sdmc" 18138 "SDMC" "sdmc" "rq1_table2"
  fi

  python3 - <<PY > "$RUN_ROOT/rq1_table2_summary.json"
from pathlib import Path
import json
root = Path("$RUN_ROOT")
rows = []
for p in sorted(root.glob("rq1_table2_*/*aggregate.json")):
    data = json.loads(p.read_text())
    parts = p.parent.name.split("_")
    dataset = parts[-1]
    model = p.parent.name.replace("rq1_table2_", "").rsplit("_", 1)[0]
    rows.append({"model": model, "dataset": dataset, **data})
for p in sorted(root.glob("rq1_table2_*_aggregate.json")):
    data = json.loads(p.read_text())
    for row in data.get("conditions", []):
        name = p.stem.replace("_aggregate", "")
        dataset = name.rsplit("_", 1)[-1]
        model = name.replace("rq1_table2_", "").rsplit("_", 1)[0]
        rows.append({"model": model, "dataset": dataset, "local_execution_match": (row.get("execution_match_pct") or 0) / 100, **row})
print(json.dumps({"rows": rows}, indent=2, ensure_ascii=False))
PY
}

select_winner_model() {
  python3 - <<PY
from pathlib import Path
import json
data = json.loads(Path("$RUN_ROOT/rq1_table2_summary.json").read_text())
scores = {}
for r in data["rows"]:
    if r["dataset"] != "spider":
        continue
    ex = r.get("local_execution_match")
    if ex is None and r.get("execution_match_pct") is not None:
        ex = r["execution_match_pct"] / 100
    if ex is not None:
        scores[r["model"]] = float(ex)
if not scores:
    raise SystemExit("no Table2 scores available")
winner = sorted(scores.items(), key=lambda x: x[1], reverse=True)[0][0]
Path("$RUN_ROOT/main_model.txt").write_text(winner + "\\n")
print(winner)
PY
}

run_conditions_with_winner() {
  local conditions="$1"
  local dry_suffix="$2"
  local out_suffix="$3"
  local winner
  winner="$(cat "$RUN_ROOT/main_model.txt")"
  dry_run_prompts "$conditions" "$dry_suffix"
  case "$winner" in
    deepseek_v4pro)
      run_deepseek_model "$winner" "$CONFIG_PRO" "$conditions" "$out_suffix"
      ;;
    deepseek_v4flash)
      run_deepseek_model "$winner" "$CONFIG_FLASH" "$conditions" "$out_suffix"
      ;;
    gemma4_26b_sdmc)
      start_local_model_server "$winner" "gemma.*(4|26b)|gemma.*26" 18114 18114 || return 1
      run_local_model "$winner" 18114 "$conditions" "$dry_suffix" "$out_suffix"
      ;;
    qwen25_14b_sdmc)
      start_local_model_server "$winner" "qwen.*2.?5.*14" 18125 18125 || return 1
      run_local_model "$winner" 18125 "$conditions" "$dry_suffix" "$out_suffix"
      ;;
    llama3_8b_sdmc)
      start_local_model_server "$winner" "llama.*3.*8b|meta.*llama.*8b" 18138 18138 || return 1
      run_local_model "$winner" 18138 "$conditions" "$dry_suffix" "$out_suffix"
      ;;
    *)
      log "unknown winner model: $winner"
      return 1
      ;;
  esac
}

write_baseline_todo() {
  cat > "$RUN_ROOT/rq1_external_baseline_status.md" <<'MD'
# RQ1 External Baseline Status

The monitor can run implemented SDMC-condition experiments automatically.
The following full external frameworks are not implemented in this repository yet and must not be reported as reproduced results until their official code is integrated and validated:

- DAIL-SQL
- DIN-SQL
- DeepEye-SQL
- CHESS full pipeline / CHESS-style isolated selector
- MAC-SQL

Safe current reproduced rows:

- RAW_SCHEMA
- TiSQL/HDC-style from validated HDC store
- SDMC

Next integration rule:

For each external method, create an adapter that emits `prompt_records.jsonl`, `predictions.jsonl`, and `executions.jsonl` under the same evaluator before adding it to Table 1.
MD
}

main_once() {
  run_step "<gpu-alias>_preflight" <gpu-alias>_preflight
  run_step "scan_models" scan_models
  run_step "copy_dev_resources" copy_dev_resources
  run_step "patch_store_paths" patch_store_paths
  run_step "rq1_table2" run_table2
  run_step "select_winner_model" select_winner_model
  run_step "rq1_table1_core_rows" run_conditions_with_winner "RAW_SCHEMA,HDC_STYLE,SDMC" "rq1_table1_core" "rq1_table1_core"
  run_step "write_baseline_todo" write_baseline_todo
  run_step "rq2_store_graph" run_conditions_with_winner "RAW_SCHEMA,SDMC_FULL,SDMC_FLAT_STORE,SDMC_GRAPH_SCHEMA_ONLY,SDMC_GRAPH_NO_REL,SDMC" "rq2" "rq2"
  run_step "rq3_available_contexts" run_conditions_with_winner "HDC_STYLE,SDMC" "rq3_available" "rq3_available"
  run_step "rq4_level_ablation" run_conditions_with_winner "SDMC_NO_COLUMN_CTX,SDMC_NO_TABLE_CTX,SDMC_NO_DATABASE_CTX,SDMC_ONLY_COLUMN_CTX,SDMC_ONLY_TABLE_CTX,SDMC_ONLY_DATABASE_CTX,SDMC" "rq4" "rq4"
  touch "$RUN_ROOT/COMPLETE"
}

log "RQ monitor started. RUN_ROOT=$RUN_ROOT interval=${INTERVAL_SECONDS}s"
while true; do
  if [ -f "$RUN_ROOT/COMPLETE" ]; then
    log "COMPLETE exists; monitor exits."
    exit 0
  fi
  if ssh_ready; then
    log "<gpu-alias> reachable; launching RQ pipeline."
    main_once
    exit 0
  fi
  log "<gpu-alias> not reachable; sleeping ${INTERVAL_SECONDS}s."
  sleep "$INTERVAL_SECONDS"
done
