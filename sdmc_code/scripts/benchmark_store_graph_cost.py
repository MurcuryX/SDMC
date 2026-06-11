#!/usr/bin/env python3
"""CPU-only Stage A cost benchmark for Context Store / Context Graph.

This writes fresh benchmark artifacts under a new output directory and never
overwrites the main experiment context stores.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]


def run_step(cmd: list[str], log_path: Path, cwd: Path) -> dict[str, object]:
    start = time.monotonic()
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.run(cmd, cwd=cwd, text=True, stdout=log, stderr=subprocess.STDOUT)
    elapsed = time.monotonic() - start
    return {
        "command": cmd,
        "returncode": proc.returncode,
        "elapsed_seconds": elapsed,
        "log": str(log_path),
    }


def count_table(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except sqlite3.Error:
        return 0


def summarize_store(store_path: Path) -> dict[str, object]:
    conn = sqlite3.connect(store_path)
    try:
        return {
            "store_path": str(store_path),
            "store_size_bytes": store_path.stat().st_size,
            "databases": count_table(conn, "databases"),
            "tables": count_table(conn, "tables"),
            "columns": count_table(conn, "columns"),
            "context_items": count_table(conn, "context_items"),
            "provenance_records": count_table(conn, "provenance_records"),
            "graph_nodes": count_table(conn, "graph_nodes"),
            "graph_edges": count_table(conn, "graph_edges"),
        }
    finally:
        conn.close()


def benchmark(dataset: str, root: Path, output_root: Path) -> dict[str, object]:
    out = output_root / dataset / "dev"
    out.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT / "src")
    py = sys.executable

    inv_cmd = [
        py,
        "-m",
        "sdmc",
        "inventory",
        "--dataset",
        dataset,
        "--split",
        "dev",
        "--root",
        str(root),
        "--output",
        str(out),
    ]
    build_cmd = [
        py,
        "-m",
        "sdmc",
        "build",
        "--dataset",
        dataset,
        "--split",
        "dev",
        "--root",
        str(root),
        "--output",
        str(out),
        "--materialize-graph",
        "--force",
    ]

    old_env = os.environ.copy()
    os.environ.update(env)
    try:
        inventory = run_step(inv_cmd, out / "inventory.log", PROJECT)
        build = run_step(build_cmd, out / "build_materialize_graph.log", PROJECT)
    finally:
        os.environ.clear()
        os.environ.update(old_env)

    summary = {
        "dataset": dataset,
        "split": "dev",
        "root": str(root),
        "output_dir": str(out),
        "inventory": inventory,
        "build_materialize_graph": build,
    }
    store_path = out / "context_store.sqlite"
    if store_path.exists():
        summary["store"] = summarize_store(store_path)
    (out / "cost_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spider-root", default=str(PROJECT / "outputs/rq_final_20260608_023504/local_data/roots/spider"))
    parser.add_argument("--bird-root", default=str(PROJECT / "outputs/rq_final_20260608_023504/local_data/roots/bird"))
    parser.add_argument("--output-root", default=str(PROJECT / "outputs/rq_final_20260608_023504/store_graph_cost_benchmark"))
    parser.add_argument("--datasets", default="spider,bird")
    args = parser.parse_args()

    output_root = Path(args.output_root) / time.strftime("%Y%m%d_%H%M%S")
    output_root.mkdir(parents=True, exist_ok=True)
    roots = {"spider": Path(args.spider_root), "bird": Path(args.bird_root)}
    summaries = []
    for dataset in [d.strip() for d in args.datasets.split(",") if d.strip()]:
        summaries.append(benchmark(dataset, roots[dataset], output_root))
    manifest = {"output_root": str(output_root), "summaries": summaries}
    (output_root / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
