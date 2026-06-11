#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sdmc.hdc import HDCStore


def compact_json(value: Any, limit: int = 4000) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(text) <= limit:
        return text
    return text[: limit - 80].rstrip() + " ... [truncated]"


def import_hdc_file(store: HDCStore, path: Path, model: str) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    database_id = data.get("db_id") or path.parent.name
    imported = 0

    database_context = data.get("database")
    if database_context:
        store.upsert(database_id, "database", compact_json(database_context), model)
        imported += 1

    for table_ctx in data.get("tables") or []:
        table = table_ctx.get("table") or table_ctx.get("table_name")
        if not table:
            continue
        store.upsert(database_id, "table", compact_json(table_ctx), model, table=table)
        imported += 1

    for column_ctx in data.get("columns") or []:
        table = column_ctx.get("table") or column_ctx.get("table_name")
        column = column_ctx.get("column") or column_ctx.get("column_name")
        if not table or not column:
            continue
        store.upsert(database_id, "column", compact_json(column_ctx), model, table=table, column=column)
        imported += 1

    return {"database_id": database_id, "path": str(path), "imported_contexts": imported}


def main() -> int:
    parser = argparse.ArgumentParser(description="Import TiInsight-style per-database hdc.json files into SDMC's SQLite HDCStore.")
    parser.add_argument("--hdc-root", required=True, help="Root containing dataset/db_id/hdc.json files.")
    parser.add_argument("--output", required=True, help="Output SQLite HDC store path.")
    parser.add_argument("--dataset", choices=["spider", "bird"], help="Optional dataset subdirectory to import.")
    parser.add_argument("--model", default="qwen2.5-generated-hdc", help="Model label stored in hdc_contexts.")
    args = parser.parse_args()

    root = Path(args.hdc_root)
    if args.dataset:
        files = sorted((root / args.dataset).glob("*/hdc.json"))
    else:
        files = sorted(root.glob("*/*/hdc.json"))
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    store = HDCStore(out)
    rows = []
    try:
        for path in files:
            rows.append(import_hdc_file(store, path, args.model))
    finally:
        store.close()

    total = sum(r["imported_contexts"] for r in rows)
    print(json.dumps({"status": "ok", "files": len(files), "contexts": total, "output": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
