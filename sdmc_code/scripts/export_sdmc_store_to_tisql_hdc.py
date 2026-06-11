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


def short_fact(context_type: str, payload: Any) -> str:
    if not isinstance(payload, dict):
        return f"{context_type}: {str(payload)[:180]}"
    parts: list[str] = [context_type]
    for key in (
        "row_count",
        "distinct_count",
        "null_count",
        "null_ratio",
        "uniqueness_ratio",
        "min_value",
        "max_value",
        "mean_value",
        "earliest_value",
        "latest_value",
        "coverage_type",
        "value_exposure_status",
        "suppression_reason",
    ):
        if key in payload:
            parts.append(f"{key}={payload[key]}")
    top_values = payload.get("top_k_values")
    if isinstance(top_values, list) and top_values:
        vals = []
        for item in top_values[:5]:
            if isinstance(item, dict):
                vals.append(f"{item.get('value')}({item.get('frequency')})")
            else:
                vals.append(str(item))
        parts.append("top_values=" + ", ".join(vals))
    return "; ".join(parts)[:900]


def fetch_rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    return list(conn.execute(sql, params).fetchall())


def export_database(conn: sqlite3.Connection, database_id: str) -> dict[str, Any]:
    db_row = conn.execute(
        "SELECT sqlite_path, table_count, column_count FROM databases WHERE database_id=? LIMIT 1",
        (database_id,),
    ).fetchone()
    sqlite_path = db_row["sqlite_path"] if db_row else ""
    table_rows = fetch_rows(
        conn,
        """
        SELECT table_name, row_count, column_count, primary_keys_json, foreign_keys_json
        FROM tables
        WHERE database_id=?
        ORDER BY table_name
        """,
        (database_id,),
    )
    col_rows = fetch_rows(
        conn,
        """
        SELECT table_name, column_name, declared_type, normalized_type, is_primary_key,
               is_foreign_key, foreign_key_target_json, inferred_profile_type
        FROM columns
        WHERE database_id=?
        ORDER BY table_name, ordinal_position
        """,
        (database_id,),
    )
    ctx_rows = fetch_rows(
        conn,
        """
        SELECT context_level, context_type, target_table, target_column, structured_result_json,
               source_operation, sql_template_id, provenance_id, exact_or_approximate
        FROM context_items
        WHERE database_id=? AND execution_status='ok'
        ORDER BY context_level, target_table, target_column, context_type
        """,
        (database_id,),
    )

    ctx_by_col: dict[tuple[str, str], list[str]] = defaultdict(list)
    ctx_by_table: dict[str, list[str]] = defaultdict(list)
    db_facts: list[str] = []
    for row in ctx_rows:
        payload = load_json(row["structured_result_json"])
        fact = short_fact(row["context_type"], payload)
        fact += f" [source={row['source_operation']}; exactness={row['exact_or_approximate']}]"
        table = row["target_table"]
        column = row["target_column"]
        if row["context_level"] == "column" and table and column:
            ctx_by_col[(table, column)].append(fact)
        elif row["context_level"] == "table" and table:
            ctx_by_table[table].append(fact)
        else:
            db_facts.append(fact)

    columns_by_table: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in col_rows:
        columns_by_table[row["table_name"]].append(row)

    columns: list[dict[str, Any]] = []
    for row in col_rows:
        facts = ctx_by_col.get((row["table_name"], row["column_name"]), [])
        samples = []
        for fact in facts:
            if "top_values=" in fact:
                samples_text = fact.split("top_values=", 1)[1].split(" [source=", 1)[0]
                samples.extend([x.rsplit("(", 1)[0] for x in samples_text.split(", ")[:3]])
        columns.append(
            {
                "db_id": database_id,
                "table": row["table_name"],
                "column": row["column_name"],
                "type": row["declared_type"] or row["normalized_type"] or "",
                "is_primary_key": bool(row["is_primary_key"]),
                "samples": samples[:5],
                "summary": (
                    f"SQL-derived facts for {row['table_name']}.{row['column_name']}: "
                    + (" | ".join(facts[:6]) if facts else "no executable SQL fact was available.")
                )[:1800],
                "semantic_type": row["inferred_profile_type"] or row["normalized_type"] or "unknown",
                "possible_meaning": "Derived from executable SQL profiling, not free-form LLM generation.",
            }
        )

    table_map = {row["table_name"]: row for row in table_rows}
    tables: list[dict[str, Any]] = []
    for table_name in sorted(columns_by_table):
        row = table_map.get(table_name)
        col_names = [c["column_name"] for c in columns_by_table[table_name]]
        primary_keys = load_json(row["primary_keys_json"]) if row else []
        foreign_keys = load_json(row["foreign_keys_json"]) if row else []
        facts = ctx_by_table.get(table_name, [])
        row_count = row["row_count"] if row else None
        tables.append(
            {
                "db_id": database_id,
                "table": table_name,
                "columns": col_names,
                "foreign_keys": foreign_keys or [],
                "description": (
                    f"SQL-derived table context for {table_name}: row_count={row_count}; "
                    + (" | ".join(facts[:8]) if facts else f"columns include {', '.join(col_names[:12])}.")
                )[:2200],
                "primary_key": ", ".join(primary_keys or []),
                "key_attributes": list((primary_keys or []) + col_names[:5])[:8],
                "table_type": "sql_profiled_relation",
                "entity": table_name,
                "relationships": [
                    f"{fk.get('table')}.{fk.get('from')} references {fk.get('ref_table')}.{fk.get('to')}"
                    for fk in (foreign_keys or [])
                    if isinstance(fk, dict)
                ],
            }
        )

    database = {
        "entities": [
            {
                "name": item["table"],
                "summary": item["description"][:300],
                "tables": [item["table"]],
                "key_attributes": item["key_attributes"],
            }
            for item in tables[:30]
        ],
        "purpose": f"SQL-derived multi-level context for database {database_id}.",
        "domain": database_id.replace("_", " "),
        "business_impact": "Supports reliable text-to-SQL by exposing executable SQL facts and provenance-aware schema relations.",
        "real_world_example": "A question-time selector can retrieve compact SQL-derived facts before SQL generation.",
        "user_friendly_description": (
            f"Database {database_id} has {len(tables)} tables and {len(columns)} columns. "
            + (" ".join(db_facts[:6]) if db_facts else "Context was derived from executable SQL profiling.")
        )[:2200],
        "summary": f"SQL-derived context for {database_id}",
        "sql_derived_facts": db_facts[:20],
    }
    return {"db_id": database_id, "sqlite_path": sqlite_path, "columns": columns, "tables": tables, "database": database}


def main() -> int:
    parser = argparse.ArgumentParser(description="Export SDMC SQL-derived Context Store to TiSQL hdc.json directories.")
    parser.add_argument("--store", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--database-id")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    out_root = Path(args.output_root) / args.dataset
    out_root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.store)
    conn.row_factory = sqlite3.Row
    try:
        if args.database_id:
            db_ids = [args.database_id]
        else:
            db_ids = [
                row["database_id"]
                for row in conn.execute("SELECT DISTINCT database_id FROM databases ORDER BY database_id")
            ]
        for db_id in db_ids:
            out_dir = out_root / db_id
            out_file = out_dir / "hdc.json"
            if out_file.exists() and not args.force:
                print(json.dumps({"database_id": db_id, "status": "skip_exists", "path": str(out_file)}))
                continue
            out_dir.mkdir(parents=True, exist_ok=True)
            hdc = export_database(conn, db_id)
            out_file.write_text(json.dumps(hdc, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"database_id": db_id, "status": "written", "path": str(out_file)}))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
