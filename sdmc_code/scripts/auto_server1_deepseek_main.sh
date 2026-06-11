#!/usr/bin/env bash
set -euo pipefail

BASE="<SDMC_ROOT>"
cd "$BASE"

INTERVAL_SECONDS="${INTERVAL_SECONDS:-600}"
RUN_ROOT="$BASE/outputs/auto_deepseek_main"
LOCAL_ROOT="$RUN_ROOT/local_data"
LOG_DIR="$RUN_ROOT/logs"
CONFIG="$BASE/configs/sdmc_deepseek_full.yaml"
API_KEY_FILE="$BASE/<API_KEY_FILE>"
HDC_STORE="$LOCAL_ROOT/context_stores/dev_hdc.sqlite"
MONITOR_LOG="$LOG_DIR/monitor.log"

mkdir -p "$LOCAL_ROOT/context_stores" "$LOCAL_ROOT/roots/spider/database" "$LOCAL_ROOT/roots/bird/dev_databases" "$LOG_DIR"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$MONITOR_LOG"
}

ssh_ready() {
  ssh -o BatchMode=yes -o ConnectTimeout=8 <gpu-alias> 'hostname >/dev/null'
}

copy_context_stores() {
  log "Copying Context Store SQLite files from <gpu-alias>."
  rsync -az <gpu-alias>:<SERVER1_DATA_ROOT>/SDMC/outputs/context_build_v2/spider/dev/context_store.sqlite "$LOCAL_ROOT/context_stores/spider_dev_context_store.sqlite"
  rsync -az <gpu-alias>:<SERVER1_DATA_ROOT>/SDMC/outputs/context_build_v2/bird/dev/context_store.sqlite "$LOCAL_ROOT/context_stores/bird_dev_context_store.sqlite"
}

copy_dev_data() {
  log "Copying Spider/BIRD dev metadata and SQLite databases to <jump-host>."
  rsync -az <gpu-alias>:<SERVER1_DATA_ROOT>/share_data/text_to_sql/spider_full/extracted/spider_data/dev.json "$LOCAL_ROOT/roots/spider/dev.json"
  rsync -az <gpu-alias>:<SERVER1_DATA_ROOT>/share_data/text_to_sql/spider_full/extracted/spider_data/tables.json "$LOCAL_ROOT/roots/spider/tables.json"
  python3 - <<'PY' > "$RUN_ROOT/spider_dev_dbs.txt"
import json
from pathlib import Path
data = json.loads(Path("outputs/auto_deepseek_main/local_data/roots/spider/dev.json").read_text())
for db in sorted({r["db_id"] for r in data}):
    print(db)
PY
  while read -r db; do
    [ -n "$db" ] || continue
    rsync -az "<gpu-alias>:<SERVER1_DATA_ROOT>/share_data/text_to_sql/spider_full/extracted/spider_data/database/$db" "$LOCAL_ROOT/roots/spider/database/"
  done < "$RUN_ROOT/spider_dev_dbs.txt"

  rsync -az <gpu-alias>:<SERVER1_DATA_ROOT>/share_data/text_to_sql/bird_full/extracted/dev_20240627/dev.json "$LOCAL_ROOT/roots/bird/dev.json"
  rsync -az <gpu-alias>:<SERVER1_DATA_ROOT>/share_data/text_to_sql/bird_full/extracted/dev_20240627/dev_tables.json "$LOCAL_ROOT/roots/bird/dev_tables.json"
  rsync -az <gpu-alias>:<SERVER1_DATA_ROOT>/share_data/text_to_sql/bird_full/extracted/dev_20240627/dev_databases/dev_databases/ "$LOCAL_ROOT/roots/bird/dev_databases/"
}

patch_local_store_paths() {
  log "Patching copied Context Store database paths to local SQLite files."
  python3 - <<'PY'
from pathlib import Path
import json
import sqlite3

base = Path("<SDMC_ROOT>")
local = base / "outputs/auto_deepseek_main/local_data"

updates = [
    (
        local / "context_stores/spider_dev_context_store.sqlite",
        "spider",
        json.loads((local / "roots/spider/dev.json").read_text()),
        lambda db: local / "roots/spider/database" / db / f"{db}.sqlite",
    ),
    (
        local / "context_stores/bird_dev_context_store.sqlite",
        "bird",
        json.loads((local / "roots/bird/dev.json").read_text()),
        lambda db: local / "roots/bird/dev_databases" / db / f"{db}.sqlite",
    ),
]

for store, _, records, path_fn in updates:
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
    if missing:
        raise SystemExit(f"missing local sqlite files: {missing[:10]}")
    print({"store": str(store), "patched_dbs": len(dbs)})
PY
}

<gpu-alias>_preflight() {
  log "Running <gpu-alias> preflight."
  ssh <gpu-alias> 'hostname; date; nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits || true'
  ssh <gpu-alias> 'cd <SERVER1_DATA_ROOT>/SDMC && python3 - <<'"'"'PY'"'"'
import sqlite3, pathlib
for p in sorted(pathlib.Path("outputs/context_build_v2").glob("*/*/context_store.sqlite")):
    conn=sqlite3.connect(p)
    total=conn.execute("select count(*) from databases").fetchone()[0]
    complete=conn.execute("select count(*) from databases where build_status in ('context_complete','graph_complete')").fetchone()[0]
    graph=conn.execute("select count(*) from dataset_graph_summary where build_status='graph_complete'").fetchone()[0]
    failed=conn.execute("select count(*) from context_items where execution_status='failed'").fetchone()[0]
    print(p, {"complete":complete,"total":total,"graph":graph,"failed_context":failed})
    conn.close()
PY'
}

qwen_gemma_preflight() {
  log "Qwen/Gemma preflight only; no GPU job will be launched."
  ssh <gpu-alias> 'find <SERVER1_DATA_ROOT>/share_model/huggingface/hub -maxdepth 1 -type d -name "models--*" | grep -Ei "Qwen|gemma" | sed "s#^$HOME#~#" | sort' | tee "$RUN_ROOT/qwen_gemma_candidates.txt"
}

generate_missing_hdc() {
  log "Generating missing HDC-style contexts for dev databases."
  python3 - <<'PY'
from pathlib import Path
import json
import os
import sqlite3
import subprocess

base = Path("<SDMC_ROOT>")
local = base / "outputs/auto_deepseek_main/local_data"
hdc = local / "context_stores/dev_hdc.sqlite"
jobs = []
for dataset, store, qfile in [
    ("spider", local / "context_stores/spider_dev_context_store.sqlite", local / "roots/spider/dev.json"),
    ("bird", local / "context_stores/bird_dev_context_store.sqlite", local / "roots/bird/dev.json"),
]:
    records = json.loads(qfile.read_text())
    for db in sorted({r.get("db_id") or r.get("database_id") for r in records}):
        jobs.append((dataset, store, db))

def existing_levels(db):
    if not hdc.exists():
        return set()
    conn = sqlite3.connect(hdc)
    try:
        rows = conn.execute("select hdc_level from hdc_contexts where database_id=?", (db,)).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()

for dataset, store, db in jobs:
    levels = existing_levels(db)
    if {"column", "table", "database"}.issubset(levels):
        print({"event": "hdc_skip", "database_id": db, "levels": sorted(levels)}, flush=True)
        continue
    print({"event": "hdc_generate", "dataset": dataset, "database_id": db}, flush=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    subprocess.run([
        "python3", "-m", "sdmc", "--config", "configs/sdmc_deepseek_full.yaml",
        "hdc-generate",
        "--store", str(store),
        "--hdc-store", str(hdc),
        "--database-id", db,
        "--api-key-file", "<API_KEY_FILE>",
        "--allow-api-calls",
    ], cwd=base, env=env, check=True)
PY
}

stage_b_dry_run() {
  log "Running Stage B full dev dry-runs."
  PYTHONPATH=src python3 -m sdmc --config "$CONFIG" run-experiment \
    --dataset spider --split dev --root "$LOCAL_ROOT/roots/spider" \
    --store "$LOCAL_ROOT/context_stores/spider_dev_context_store.sqlite" \
    --output "$RUN_ROOT/dry_spider_dev" \
    --conditions RAW_SCHEMA,HDC_STYLE,SDMC \
    --hdc-store "$HDC_STORE"
  PYTHONPATH=src python3 -m sdmc --config "$CONFIG" run-experiment \
    --dataset bird --split dev --root "$LOCAL_ROOT/roots/bird" \
    --store "$LOCAL_ROOT/context_stores/bird_dev_context_store.sqlite" \
    --output "$RUN_ROOT/dry_bird_dev" \
    --conditions RAW_SCHEMA,HDC_STYLE,SDMC \
    --hdc-store "$HDC_STORE"
}

run_deepseek_main() {
  log "Starting DeepSeek main experiment: Spider dev RAW_SCHEMA/HDC_STYLE/SDMC."
  PYTHONPATH=src python3 -m sdmc --config "$CONFIG" run-experiment \
    --dataset spider --split dev --root "$LOCAL_ROOT/roots/spider" \
    --store "$LOCAL_ROOT/context_stores/spider_dev_context_store.sqlite" \
    --output "$RUN_ROOT/deepseek_spider_dev_raw_hdc_sdmc" \
    --conditions RAW_SCHEMA,HDC_STYLE,SDMC \
    --api-key-file "$API_KEY_FILE" \
    --hdc-store "$HDC_STORE" \
    --real-run --allow-api-calls
  PYTHONPATH=src python3 -m sdmc --config "$CONFIG" report --kind aggregate --output "$RUN_ROOT/deepseek_spider_dev_raw_hdc_sdmc"

  log "Starting DeepSeek main experiment: BIRD dev RAW_SCHEMA/HDC_STYLE/SDMC."
  PYTHONPATH=src python3 -m sdmc --config "$CONFIG" run-experiment \
    --dataset bird --split dev --root "$LOCAL_ROOT/roots/bird" \
    --store "$LOCAL_ROOT/context_stores/bird_dev_context_store.sqlite" \
    --output "$RUN_ROOT/deepseek_bird_dev_raw_hdc_sdmc" \
    --conditions RAW_SCHEMA,HDC_STYLE,SDMC \
    --api-key-file "$API_KEY_FILE" \
    --hdc-store "$HDC_STORE" \
    --real-run --allow-api-calls
  PYTHONPATH=src python3 -m sdmc --config "$CONFIG" report --kind aggregate --output "$RUN_ROOT/deepseek_bird_dev_raw_hdc_sdmc"
}

main_once() {
  <gpu-alias>_preflight
  qwen_gemma_preflight
  copy_context_stores
  copy_dev_data
  patch_local_store_paths
  stage_b_dry_run
  generate_missing_hdc
  run_deepseek_main
  touch "$RUN_ROOT/COMPLETE"
  log "Auto DeepSeek main experiment completed."
}

log "Auto monitor started. interval=${INTERVAL_SECONDS}s"
while true; do
  if [ -f "$RUN_ROOT/COMPLETE" ]; then
    log "COMPLETE exists; exiting monitor."
    exit 0
  fi
  if ssh_ready; then
    log "<gpu-alias> reachable; launching pipeline."
    main_once
    exit 0
  fi
  log "<gpu-alias> not reachable; sleeping ${INTERVAL_SECONDS}s."
  sleep "$INTERVAL_SECONDS"
done
