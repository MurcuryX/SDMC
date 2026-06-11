#!/usr/bin/env python3
"""Benchmark RQ3 context generation speed.

The benchmark separates three construction styles:
- Schema-based: deterministic schema/HDC export over all dev databases.
- SQL-based: reuses audited Stage A Context Store + Graph build timing.
- LLM-based: controlled sampled generation, because historical full HDC files
  do not preserve auditable per-run timing.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

from sdmc.config import load_config
from sdmc.hdc import build_hdc_prompt, schema_text_from_store
from sdmc.stage_b import DeepSeekAdapter


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_BASE = PROJECT / "outputs/rq_final_20260608_023504"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def latest_manifest(root: Path) -> dict | None:
    manifests = sorted(root.glob("*/manifest.json"))
    return read_json(manifests[-1]) if manifests else None


def db_ids_from_store(store: Path, limit: int | None = None) -> list[str]:
    import sqlite3

    con = sqlite3.connect(store)
    try:
        rows = [r[0] for r in con.execute("SELECT database_id FROM databases ORDER BY database_id")]
    finally:
        con.close()
    return rows[:limit] if limit else rows


def run_schema_export(dataset: str, store: Path, output_root: Path) -> dict:
    out = output_root / "schema_based" / dataset
    cmd = [
        sys.executable,
        str(PROJECT / "scripts/export_schema_to_tisql_hdc.py"),
        "--store",
        str(store),
        "--dataset",
        dataset,
        "--output-root",
        str(out),
        "--sample-limit",
        "0",
        "--force",
    ]
    start = time.monotonic()
    proc = subprocess.run(cmd, cwd=PROJECT, text=True, capture_output=True)
    elapsed = time.monotonic() - start
    log = out / "schema_export.log"
    out.mkdir(parents=True, exist_ok=True)
    log.write_text(proc.stdout + "\n--- STDERR ---\n" + proc.stderr, encoding="utf-8")
    files = sorted((out / dataset).glob("*/hdc.json"))
    total_bytes = sum(p.stat().st_size for p in files)
    return {
        "context_type": "Schema-based",
        "dataset": dataset,
        "scope": "full-dev",
        "databases": len(files),
        "api_calls": 0,
        "elapsed_seconds": elapsed,
        "seconds_per_database": elapsed / len(files) if files else None,
        "output_bytes": total_bytes,
        "status": "ok" if proc.returncode == 0 else f"failed:{proc.returncode}",
        "log": str(log),
    }


def sql_based_rows(base: Path) -> list[dict]:
    rows = []
    spider_manifest = latest_manifest(base / "store_graph_cost_benchmark_<gpu-alias>_cpu")
    bird_manifest = latest_manifest(base / "store_graph_cost_benchmark")
    if spider_manifest:
        summary = spider_manifest["summaries"][0]
        store = summary["store"]
        elapsed = summary["inventory"]["elapsed_seconds"] + summary["build_materialize_graph"]["elapsed_seconds"]
        rows.append({
            "context_type": "SQL-based (SDMC Store+Graph)",
            "dataset": "spider",
            "scope": "full-dev",
            "databases": store["databases"],
            "api_calls": 0,
            "elapsed_seconds": elapsed,
            "seconds_per_database": elapsed / store["databases"],
            "output_bytes": store["store_size_bytes"],
            "status": "ok",
            "log": summary["build_materialize_graph"]["log"],
        })
    if bird_manifest:
        for summary in bird_manifest["summaries"]:
            if summary["dataset"] == "bird":
                store = summary["store"]
                elapsed = summary["inventory"]["elapsed_seconds"] + summary["build_materialize_graph"]["elapsed_seconds"]
                rows.append({
                    "context_type": "SQL-based (SDMC Store+Graph)",
                    "dataset": "bird",
                    "scope": "full-dev",
                    "databases": store["databases"],
                    "api_calls": 0,
                    "elapsed_seconds": elapsed,
                    "seconds_per_database": elapsed / store["databases"],
                    "output_bytes": store["store_size_bytes"],
                    "status": "ok",
                    "log": summary["build_materialize_graph"]["log"],
                })
    return rows


def run_llm_sample(dataset: str, store: Path, output_root: Path, config_path: Path, api_key_file: Path, sample_dbs: int, allow_api_calls: bool) -> dict:
    config = load_config(config_path)
    adapter = DeepSeekAdapter(config, api_key_file)
    db_ids = db_ids_from_store(store, sample_dbs)
    out_dir = output_root / "llm_based_sample" / dataset
    out_dir.mkdir(parents=True, exist_ok=True)
    records = []
    start_total = time.monotonic()
    total_bytes = 0
    statuses: list[str] = []
    api_calls = 0
    for db_id in db_ids:
        schema_text = schema_text_from_store(store, db_id)
        db_payload = {"db_id": db_id, "levels": {}}
        for level in ["column", "table", "database"]:
            prompt = build_hdc_prompt(db_id, schema_text, level)
            start = time.monotonic()
            result = adapter.generate(prompt, allow_api_calls=allow_api_calls)
            elapsed = time.monotonic() - start
            api_calls += 1
            statuses.append(str(result.get("status")))
            text = result.get("raw_response") or ""
            db_payload["levels"][level] = text
            total_bytes += len(text.encode("utf-8"))
            records.append({
                "dataset": dataset,
                "database_id": db_id,
                "level": level,
                "status": result.get("status"),
                "elapsed_seconds": elapsed,
                "usage": result.get("usage", {}),
                "error": result.get("error"),
            })
        (out_dir / f"{db_id}.json").write_text(json.dumps(db_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    elapsed_total = time.monotonic() - start_total
    (out_dir / "records.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
        encoding="utf-8",
    )
    return {
        "context_type": "LLM-based (HDC-style)",
        "dataset": dataset,
        "scope": f"sampled-{len(db_ids)}db",
        "databases": len(db_ids),
        "api_calls": api_calls,
        "elapsed_seconds": elapsed_total,
        "seconds_per_database": elapsed_total / len(db_ids) if db_ids else None,
        "output_bytes": total_bytes,
        "status": "ok" if all(s == "success" for s in statuses) else ",".join(sorted(set(statuses))),
        "log": str(out_dir / "records.jsonl"),
    }


def write_outputs(rows: list[dict], output_root: Path) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    fields = [
        "context_type",
        "dataset",
        "scope",
        "databases",
        "api_calls",
        "elapsed_seconds",
        "seconds_per_database",
        "output_bytes",
        "status",
        "log",
    ]
    csv_path = output_root / "rq3_context_generation_speed.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fields})
    md = [
        "# RQ3 Context Generation Speed\n",
        "",
        "| Context type | Dataset | Scope | DBs | API calls | Elapsed sec | Sec/DB | Output MB | Status |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in rows:
        out_mb = (r.get("output_bytes") or 0) / (1024 * 1024)
        sec_db = r.get("seconds_per_database")
        sec_db_text = f"{float(sec_db):.2f}" if sec_db is not None else "---"
        md.append(
            f"| {r['context_type']} | {r['dataset']} | {r['scope']} | {r['databases']} | {r['api_calls']} | "
            f"{float(r['elapsed_seconds']):.2f} | {sec_db_text} | "
            f"{out_mb:.2f} | {r['status']} |"
        )
    md.append("")
    md.append("Note: LLM-based rows are controlled sampled generation because the historical full HDC files do not contain auditable generation logs.")
    (output_root / "rq3_context_generation_speed.md").write_text("\n".join(md), encoding="utf-8")
    (output_root / "rq3_context_generation_speed.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=str(DEFAULT_BASE))
    parser.add_argument("--output", default=str(DEFAULT_BASE / "rq3_context_generation_speed"))
    parser.add_argument("--config", default=str(PROJECT / "configs/sdmc_deepseek_hdc.yaml"))
    parser.add_argument("--api-key-file", default=str(PROJECT / "<API_KEY_FILE>"))
    parser.add_argument("--llm-sample-dbs", type=int, default=3)
    parser.add_argument("--allow-api-calls", action="store_true")
    args = parser.parse_args()

    base = Path(args.base)
    output_root = Path(args.output) / time.strftime("%Y%m%d_%H%M%S")
    stores = {
        "spider": base / "local_data/context_stores/spider_dev_context_store.sqlite",
        "bird": base / "local_data/context_stores/bird_dev_context_store.sqlite",
    }
    rows = []
    for dataset, store in stores.items():
        rows.append(run_schema_export(dataset, store, output_root))
    rows.extend(sql_based_rows(base))
    for dataset, store in stores.items():
        rows.append(run_llm_sample(dataset, store, output_root, Path(args.config), Path(args.api_key_file), args.llm_sample_dbs, args.allow_api_calls))
    write_outputs(rows, output_root)
    print(json.dumps({"output": str(output_root), "rows": rows}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
