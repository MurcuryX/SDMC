#!/usr/bin/env bash
set -u

LOCAL_BASE="<SDMC_ROOT>"
LOG="$LOCAL_BASE/outputs/current_experiments/logs/<gpu-alias>_gemma3_bird_full_monitor.log"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-600}"

REMOTE_BASE='$HOME/Backup/SDMC_remote_run'
SERVICE_TMUX="sdmc_gemma3_bird_full"
RUN_TMUX="sdmc_remote_gemma3_bird_full_conditions"
MODEL_PATH='$HOME/Backup/share_model/huggingface/hub/models--google--gemma-3-12b-it/snapshots/96b6f1eccf38110c56df3a15bffe176da04bfd80'
VLLM_BIN="/data/shared_envs/vllm-0.21-gemma4/bin/vllm"
PORT="18116"
TARGET_LINES=4602

log() {
  printf '[%s] %s\n' "$(date '+%F %T %Z')" "$*" | tee -a "$LOG"
}

remote() {
  ssh -o BatchMode=yes -o ConnectTimeout=8 <gpu-alias> "$@"
}

log "monitor started; interval=${INTERVAL_SECONDS}s"

while true; do
  if ! remote 'echo <gpu-alias>-ok' >/dev/null 2>&1; then
    log "<gpu-alias> unreachable; retry later"
    sleep "$INTERVAL_SECONDS"
    continue
  fi

  log "<gpu-alias> reachable"

  remote "
set -u
REMOTE_BASE=$REMOTE_BASE
SERVICE_TMUX=$SERVICE_TMUX
RUN_TMUX=$RUN_TMUX
MODEL_PATH=$MODEL_PATH
VLLM_BIN=$VLLM_BIN
PORT=$PORT
TARGET_LINES=$TARGET_LINES

cd \"\$REMOTE_BASE\" || exit 10
mkdir -p outputs/logs

OUT=outputs/model_compare/gemma3_12b_bird_full_conditions
LINES=0
if [ -f \"\$OUT/executions.jsonl\" ]; then
  LINES=\$(wc -l < \"\$OUT/executions.jsonl\")
fi
echo \"current_lines=\$LINES/\$TARGET_LINES\"

if [ \"\$LINES\" -ge \"\$TARGET_LINES\" ]; then
  PYTHONPATH=src python3 - <<'PY'
import json
from collections import defaultdict, Counter
from pathlib import Path
p = Path('outputs/model_compare/gemma3_12b_bird_full_conditions/executions.jsonl')
rows = [json.loads(l) for l in p.open() if l.strip()]
by = defaultdict(list)
for r in rows:
    by[r.get('condition_id')].append(r)
for cond in ['RAW_SCHEMA', 'HDC_STYLE', 'SDMC']:
    xs = by[cond]
    if not xs:
        continue
    ex = sum(r.get('execution_match') is True for r in xs) / len(xs)
    non = sum(r.get('execution_status') != 'success' for r in xs) / len(xs)
    print(f'{cond} n={len(xs)} EX={ex:.4f} non_success={non:.4f} status={Counter(r.get(\"execution_status\") for r in xs).most_common()}')
PY
  exit 0
fi

# Keep BIRD DB paths correct.
python3 - <<'PY'
import sqlite3
store = 'local_data/context_stores/bird_dev_context_store.sqlite'
old = '<SERVER1_DATA_ROOT>/share_data/text_to_sql/bird_full/extracted/dev_20240627/dev_databases'
new = '<SERVER1_DATA_ROOT>/share_data/text_to_sql/bird_full/extracted/dev_20240627/dev_databases/dev_databases'
con = sqlite3.connect(store)
con.execute('update databases set sqlite_path=replace(sqlite_path, ?, ?) where sqlite_path like ? and sqlite_path not like ?', (old, new, old + '%', new + '%'))
con.commit()
con.close()
PY

if ! curl -sS --max-time 5 \"http://127.0.0.1:\$PORT/v1/models\" >/dev/null 2>&1; then
  if tmux has-session -t \"\$SERVICE_TMUX\" 2>/dev/null; then
    echo \"service_starting_or_not_ready\"
  else
    GPU3_USED=\$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i 3 2>/dev/null | tr -d ' ')
    if [ -n \"\$GPU3_USED\" ] && [ \"\$GPU3_USED\" -lt 2000 ]; then
      echo \"starting_gemma3_service_on_gpu3\"
      tmux new -d -s \"\$SERVICE_TMUX\" \"export PATH=/data/shared_envs/vllm-0.21-gemma4/bin:\\\$PATH; CUDA_VISIBLE_DEVICES=3 \$VLLM_BIN serve \$MODEL_PATH --host 127.0.0.1 --port \$PORT --served-model-name gemma3_12b_bird_full --max-model-len 8192 --gpu-memory-utilization 0.90 > \$HOME/Backup/SDMC/logs/vllm_gemma3_bird_full.log 2>&1\"
    else
      echo \"gpu3_not_free_or_unknown; used=\$GPU3_USED; waiting\"
    fi
  fi
  exit 0
fi

if tmux has-session -t \"\$RUN_TMUX\" 2>/dev/null; then
  echo \"run_already_active\"
  exit 0
fi

echo \"starting_or_resuming_gemma3_bird_full_run\"
tmux new -d -s \"\$RUN_TMUX\" \"cd \$HOME/Backup/SDMC_remote_run && PYTHONPATH=src python3 scripts/run_local_model_calibration.py --dataset bird --split dev --root <SERVER1_DATA_ROOT>/share_data/text_to_sql/bird_full/extracted/dev_20240627 --store local_data/context_stores/bird_dev_context_store.sqlite --dry-output dry_v2_bird_dev --output outputs/model_compare/gemma3_12b_bird_full_conditions --endpoint http://127.0.0.1:\$PORT/v1 --model gemma3_12b_bird_full --model-label gemma3_12b --conditions RAW_SCHEMA,HDC_STYLE,SDMC --sample 20000 --seed 13 --max-output-tokens 512 --temperature 0 --timeout 180 > outputs/logs/gemma3_12b_bird_full_conditions.log 2>&1\"
" 2>&1 | tee -a "$LOG"

  if grep -q "EX=" "$LOG" && remote "cd $REMOTE_BASE && [ \$(wc -l < outputs/model_compare/gemma3_12b_bird_full_conditions/executions.jsonl 2>/dev/null || echo 0) -ge $TARGET_LINES ]" >/dev/null 2>&1; then
    log "target complete; monitor exiting"
    exit 0
  fi

  sleep "$INTERVAL_SECONDS"
done
