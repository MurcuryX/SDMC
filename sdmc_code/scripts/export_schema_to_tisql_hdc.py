#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any


def load_json(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def sample_values(sqlite_path: str, table: str, column: str, limit: int) -> list[str]:
    if not sqlite_path or limit <= 0 or not Path(sqlite_path).exists():
        return []
    try:
        conn = sqlite3.connect(sqlite_path)
        cur = conn.execute(
            f"SELECT DISTINCT {quote_ident(column)} FROM {quote_ident(table)} "
            f"WHERE {quote_ident(column)} IS NOT NULL LIMIT ?",
            (limit,),
        )
        out = [str(row[0])[:80] for row in cur.fetchall()]
        conn.close()
        return out
    except Exception:
        return []


def export_database(conn: sqlite3.Connection, database_id: str, sample_limit: int) -> dict[str, Any]:
    db_row = conn.execute(
        "SELECT sqlite_path, table_count, column_count FROM databases WHERE database_id=? LIMIT 1",
        (database_id,),
    ).fetchone()
    sqlite_path = db_row["sqlite_path"] if db_row else ""
    table_rows = list(
        conn.execute(
            """
            SELECT table_name, row_count, column_count, primary_keys_json, foreign_keys_json
            FROM tables
            WHERE database_id=?
            ORDER BY table_name
            """,
            (database_id,),
        )
    )
    col_rows = list(
        conn.execute(
            """
            SELECT table_name, column_name, declared_type, normalized_type, is_primary_key,
                   is_foreign_key, foreign_key_target_json, inferred_profile_type
            FROM columns
            WHERE database_id=?
            ORDER BY table_name, ordinal_position
            """,
            (database_id,),
        )
    )
    cols_by_table: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in col_rows:
        cols_by_table[row["table_name"]].append(row)

    columns: list[dict[str, Any]] = []
    for row in col_rows:
        samples = sample_values(sqlite_path, row["table_name"], row["column_name"], sample_limit)
        dtype = row["declared_type"] or row["normalized_type"] or ""
        summary = f"Column {row['table_name']}.{row['column_name']} stores {dtype or 'values'}."
        if samples:
            summary += " Sample values: " + ", ".join(samples[:3]) + "."
        columns.append(
            {
                "db_id": database_id,
                "table": row["table_name"],
                "column": row["column_name"],
                "type": dtype,
                "is_primary_key": bool(row["is_primary_key"]),
                "samples": samples,
                "summary": summary[:1000],
            }
        )

    table_map = {row["table_name"]: row for row in table_rows}
    tables: list[dict[str, Any]] = []
    for table_name, rows in sorted(cols_by_table.items()):
        table = table_map.get(table_name)
        col_names = [row["column_name"] for row in rows]
        primary_keys = load_json(table["primary_keys_json"]) if table else []
        foreign_keys = load_json(table["foreign_keys_json"]) if table else []
        relationships = [
            f"{fk.get('table')}.{fk.get('from')} references {fk.get('ref_table')}.{fk.get('to')}"
            for fk in (foreign_keys or [])
            if isinstance(fk, dict)
        ]
        tables.append(
            {
                "db_id": database_id,
                "table": table_name,
                "columns": col_names,
                "foreign_keys": foreign_keys or [],
                "description": (
                    f"Schema metadata for {table_name}: columns include {', '.join(col_names[:16])}."
                )[:1200],
                "primary_key": ", ".join(primary_keys or []),
                "key_attributes": list((primary_keys or []) + col_names[:5])[:8],
                "table_type": "schema_relation",
                "entity": table_name,
                "relationships": relationships,
            }
        )

    database = {
        "entities": [
            {
                "name": table["table"],
                "summary": table["description"],
                "tables": [table["table"]],
                "key_attributes": table["key_attributes"],
            }
            for table in tables[:30]
        ],
        "purpose": f"Schema-based context for database {database_id}.",
        "domain": database_id.replace("_", " "),
        "business_impact": "Provides table, column, key, and sample-value metadata for text-to-SQL.",
        "real_world_example": "A text-to-SQL model can inspect table and column metadata before SQL generation.",
        "user_friendly_description": (
            f"Database {database_id} has {len(tables)} tables and {len(columns)} columns."
        ),
        "summary": f"Schema-based context for {database_id}",
    }
    return {"db_id": database_id, "sqlite_path": sqlite_path, "columns": columns, "tables": tables, "database": database}


def main() -> int:
    parser = argparse.ArgumentParser(description="Export schema/sample metadata to TiSQL hdc.json directories.")
    parser.add_argument("--store", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--sample-limit", type=int, default=3)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    out_root = Path(args.output_root) / args.dataset
    out_root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.store)
    conn.row_factory = sqlite3.Row
    try:
        db_ids = [
            row["database_id"]
            for row in conn.execute("SELECT DISTINCT database_id FROM databases ORDER BY database_id")
        ]
        for db_id in db_ids:
            out_file = out_root / db_id / "hdc.json"
            if out_file.exists() and not args.force:
                print(json.dumps({"database_id": db_id, "status": "skip_exists", "path": str(out_file)}))
                continue
            out_file.parent.mkdir(parents=True, exist_ok=True)
            hdc = export_database(conn, db_id, args.sample_limit)
            out_file.write_text(json.dumps(hdc, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"database_id": db_id, "status": "written", "path": str(out_file)}))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
