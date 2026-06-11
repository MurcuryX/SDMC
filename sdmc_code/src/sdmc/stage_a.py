from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import re
import time

from sdmc.config import SDMCConfig
from sdmc.datasets import build_inventory, dataset_metadata_fks, spider_schema_metadata, bird_schema_metadata
from sdmc.sqlite_utils import fetch_all, fetch_one, open_sqlite_readonly, quote_ident, stable_hash, stable_id
from sdmc.store import ContextStore
from sdmc.types import ColumnMeta, ContextItem, ForeignKey, ProvenanceRecord, TableMeta


NUMERIC_TYPES = {"integer", "int", "real", "numeric", "decimal", "float", "double"}
TEXT_TYPES = {"text", "char", "varchar", "string", "clob"}
SENSITIVE_PAT = re.compile(r"(name|email|phone|address|street|zip|contact|admin)", re.I)
IDENTIFIER_PAT = re.compile(r"(^id$|_id$|id$|code$|uuid|key$|number$)", re.I)
TEMPORAL_PAT = re.compile(r"(date|time|year|period|month|day)", re.I)


def normalize_declared_type(declared: str | None) -> str:
    if not declared:
        return "unknown"
    text = declared.lower().strip()
    for key in NUMERIC_TYPES:
        if key in text:
            return "numeric"
    if "date" in text or "time" in text or "year" in text:
        return "temporal"
    for key in TEXT_TYPES:
        if key in text:
            return "text"
    return text or "unknown"


def classify_column(col: ColumnMeta) -> None:
    name = col.column_name
    col.normalized_type = normalize_declared_type(col.declared_type)
    col.is_identifier_like = bool(IDENTIFIER_PAT.search(name))
    col.is_sensitive_like = bool(SENSITIVE_PAT.search(name))
    col.is_long_text_like = col.normalized_type == "text" and re.search(r"(description|comment|note|summary|content|text)$", name, re.I) is not None
    if TEMPORAL_PAT.search(name) and col.normalized_type in {"text", "unknown"}:
        col.inferred_profile_type = "temporal"
        col.type_confidence = 0.6
    elif col.normalized_type in {"numeric", "temporal", "text"}:
        col.inferred_profile_type = col.normalized_type
        col.type_confidence = 0.8
    else:
        col.inferred_profile_type = "unknown"
        col.type_confidence = 0.2


def extract_catalog(sqlite_path: Path, database_id: str, metadata_fks: list[ForeignKey], timeout: float = 30.0) -> list[TableMeta]:
    metadata_by_table: dict[str, list[ForeignKey]] = {}
    for fk in metadata_fks:
        metadata_by_table.setdefault("", [])
        # Dataset metadata FK objects currently lack source table; reconcile by local column later.
    with open_sqlite_readonly(sqlite_path, timeout_seconds=timeout) as conn:
        table_names = [r["name"] for r in fetch_all(
            conn,
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
        )]
        tables: list[TableMeta] = []
        for table_name in table_names:
            info = fetch_all(conn, f"PRAGMA table_info({quote_ident(table_name)})")
            sqlite_fks = fetch_all(conn, f"PRAGMA foreign_key_list({quote_ident(table_name)})")
            fks = [
                ForeignKey(
                    source_table=table_name,
                    local_column=str(row["from"]),
                    referenced_table=str(row["table"]),
                    referenced_column=str(row["to"]),
                    source="sqlite_declared",
                )
                for row in sqlite_fks
            ]
            # Add dataset metadata FK when local column name matches. This records extra catalog provenance.
            for mfk in metadata_fks:
                if mfk.source_table == table_name and any(c["name"] == mfk.local_column for c in info):
                    if not any(f.local_column == mfk.local_column and f.referenced_table == mfk.referenced_table for f in fks):
                        fks.append(mfk)
            table = TableMeta(table_name=table_name)
            table.foreign_keys = fks
            for row in info:
                col_fks = [fk for fk in fks if fk.local_column == row["name"]]
                col = ColumnMeta(
                    table_name=table_name,
                    column_name=str(row["name"]),
                    ordinal_position=int(row["cid"]),
                    declared_type=row["type"],
                    nullable=not bool(row["notnull"]),
                    default_value=row["dflt_value"],
                    primary_key_position=int(row["pk"] or 0),
                    foreign_keys=col_fks,
                )
                classify_column(col)
                table.columns.append(col)
            table.primary_keys = [c.column_name for c in table.columns if c.primary_key_position]
            tables.append(table)
        return tables


def provenance(database_id: str, snapshot_id: str, source_type: str, op: str, template_id: str | None, sql: str | None, status: str, elapsed_ms: float | None, exact: str = "exact", error: str | None = None) -> ProvenanceRecord:
    return ProvenanceRecord(
        provenance_id=stable_id("prov", database_id, snapshot_id, op, template_id or "catalog", stable_hash(sql or op)),
        database_id=database_id,
        snapshot_id=snapshot_id,
        source_type=source_type,
        source_operation=op,
        sql_template_id=template_id,
        executed_sql=sql,
        execution_status=status,
        execution_time_ms=elapsed_ms,
        exact_or_approximate=exact,
        error_message=error,
    )


def context(database_id: str, snapshot_id: str, level: str, ctype: str, result: dict[str, Any], op: str, status: str, exact: str, table: str | None = None, column: str | None = None, template_id: str | None = None, prov_id: str | None = None) -> ContextItem:
    return ContextItem(
        context_id=stable_id("ctx", database_id, snapshot_id, level, ctype, table, column, stable_hash(json.dumps(result, sort_keys=True, default=str))),
        database_id=database_id,
        snapshot_id=snapshot_id,
        context_level=level,
        context_type=ctype,
        target_table=table,
        target_column=column,
        structured_result=result,
        source_operation=op,
        sql_template_id=template_id,
        provenance_id=prov_id,
        execution_status=status,
        exact_or_approximate=exact,
    )


def fallback_sample_profile(conn, database_id: str, snapshot_id: str, table: str, col: ColumnMeta, config: SDMCConfig, error: Exception, start: float) -> tuple[list[ContextItem], list[ProvenanceRecord]]:
    q_table = quote_ident(table)
    q_col = quote_ident(col.column_name)
    limit = int(config.profiling.sample_limit)
    sql = f"SELECT {q_col} AS value FROM {q_table} WHERE {q_col} IS NOT NULL LIMIT {limit}"
    values = [r["value"] for r in fetch_all(conn, sql, timeout_seconds=max(5.0, config.profiling.max_column_profile_seconds / 2))]
    result: dict[str, Any] = {
        "fallback_reason": str(error),
        "sample_size": len(values),
        "sample_limit": limit,
        "sample_profile": True,
    }
    if col.inferred_profile_type == "numeric":
        nums = [float(v) for v in values if v is not None]
        result.update({
            "sample_min_value": min(nums) if nums else None,
            "sample_max_value": max(nums) if nums else None,
            "sample_mean_value": (sum(nums) / len(nums)) if nums else None,
            "null_ratio": None,
        })
        template_id = "numeric_sample_fallback_v1"
        ctype = "numeric_profile"
    elif col.inferred_profile_type == "temporal":
        text_values = [str(v) for v in values if v is not None]
        result.update({
            "sample_earliest_value": min(text_values) if text_values else None,
            "sample_latest_value": max(text_values) if text_values else None,
            "coverage_type": "period_like" if TEMPORAL_PAT.search(col.column_name) else "temporal_like",
            "null_ratio": None,
        })
        template_id = "temporal_sample_fallback_v1"
        ctype = "temporal_profile"
    else:
        distinct_sample = len({str(v) for v in values})
        result.update({
            "sample_distinct_count": distinct_sample,
            "sample_uniqueness_ratio": distinct_sample / len(values) if values else None,
            "null_ratio": None,
        })
        ctype = "identifier_profile" if col.is_identifier_like else "categorical_profile"
        template_id = "distinct_sample_fallback_v1"
        if col.is_sensitive_like or col.is_identifier_like or col.is_long_text_like:
            result["value_exposure_status"] = "suppressed"
            result["suppression_reason"] = "sensitive_or_identifier_or_long_text"
        else:
            counts: dict[str, int] = {}
            for value in values:
                key = str(value)
                counts[key] = counts.get(key, 0) + 1
            top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[: config.profiling.topk_limit]
            result["top_k_values"] = [{"value": k, "frequency": v, "sample_based": True} for k, v in top]
            result["value_exposure_status"] = "safe_observed_values_sample"
    p = provenance(
        database_id,
        snapshot_id,
        "executed_sql",
        "profile_column_sample_fallback",
        template_id,
        sql,
        "success",
        (time.monotonic() - start) * 1000,
        "approximate",
        str(error),
    )
    item = context(database_id, snapshot_id, "column", ctype, result, "profile_column_sample_fallback", "success", "approximate", table, col.column_name, template_id, p.provenance_id)
    return [item], [p]


def execute_profile(conn, database_id: str, snapshot_id: str, table: str, col: ColumnMeta, budget: SDMCConfig) -> tuple[list[ContextItem], list[ProvenanceRecord]]:
    items: list[ContextItem] = []
    provs: list[ProvenanceRecord] = []
    q_table = quote_ident(table)
    q_col = quote_ident(col.column_name)
    op = "profile_column"
    start = time.monotonic()
    try:
        if col.inferred_profile_type == "numeric":
            sql = f'SELECT COUNT(*) row_count, SUM(CASE WHEN {q_col} IS NULL THEN 1 ELSE 0 END) null_count, MIN({q_col}) min_value, MAX({q_col}) max_value, AVG({q_col}) mean_value FROM {q_table}'
            row = fetch_one(conn, sql, timeout_seconds=budget.profiling.max_column_profile_seconds)
            result = dict(row) if row else {}
            null_count = result.get("null_count") or 0
            row_count = result.get("row_count") or 0
            result["null_ratio"] = null_count / row_count if row_count else None
            p = provenance(database_id, snapshot_id, "executed_sql", op, "numeric_profile_v1", sql, "success", (time.monotonic() - start) * 1000)
            provs.append(p)
            items.append(context(database_id, snapshot_id, "column", "numeric_profile", result, op, "success", "exact", table, col.column_name, "numeric_profile_v1", p.provenance_id))
        elif col.inferred_profile_type == "temporal":
            sql = f'SELECT COUNT(*) row_count, SUM(CASE WHEN {q_col} IS NULL THEN 1 ELSE 0 END) null_count, MIN({q_col}) earliest_value, MAX({q_col}) latest_value FROM {q_table}'
            row = fetch_one(conn, sql, timeout_seconds=budget.profiling.max_column_profile_seconds)
            result = dict(row) if row else {}
            row_count = result.get("row_count") or 0
            null_count = result.get("null_count") or 0
            result["null_ratio"] = null_count / row_count if row_count else None
            result["coverage_type"] = "period_like" if TEMPORAL_PAT.search(col.column_name) else "temporal_like"
            p = provenance(database_id, snapshot_id, "executed_sql", op, "temporal_profile_v1", sql, "success", (time.monotonic() - start) * 1000)
            provs.append(p)
            items.append(context(database_id, snapshot_id, "column", "temporal_profile", result, op, "success", "exact", table, col.column_name, "temporal_profile_v1", p.provenance_id))
        else:
            sql = f'SELECT COUNT(*) row_count, COUNT(DISTINCT {q_col}) distinct_count, SUM(CASE WHEN {q_col} IS NULL THEN 1 ELSE 0 END) null_count FROM {q_table}'
            row = fetch_one(conn, sql, timeout_seconds=budget.profiling.max_column_profile_seconds)
            result = dict(row) if row else {}
            row_count = result.get("row_count") or 0
            null_count = result.get("null_count") or 0
            distinct = result.get("distinct_count") or 0
            result["null_ratio"] = null_count / row_count if row_count else None
            result["uniqueness_ratio"] = distinct / row_count if row_count else None
            p = provenance(database_id, snapshot_id, "executed_sql", op, "distinct_profile_v1", sql, "success", (time.monotonic() - start) * 1000)
            provs.append(p)
            ctype = "identifier_profile" if col.is_identifier_like else "categorical_profile"
            if col.is_sensitive_like or col.is_identifier_like or col.is_long_text_like or distinct > budget.profiling.max_distinct_before_topk:
                result["value_exposure_status"] = "suppressed"
                result["suppression_reason"] = "sensitive_or_identifier_or_high_cardinality"
            elif distinct <= budget.profiling.max_distinct_before_topk:
                topk_sql = f'SELECT {q_col} value, COUNT(*) frequency FROM {q_table} WHERE {q_col} IS NOT NULL GROUP BY {q_col} ORDER BY frequency DESC, value ASC LIMIT {int(budget.profiling.topk_limit)}'
                values = [dict(r) for r in fetch_all(conn, topk_sql, timeout_seconds=budget.profiling.max_column_profile_seconds)]
                result["top_k_values"] = values
                result["value_exposure_status"] = "safe_observed_values"
            items.append(context(database_id, snapshot_id, "column", ctype, result, op, "success", "exact", table, col.column_name, "distinct_profile_v1", p.provenance_id))
    except Exception as e:
        try:
            fallback_items, fallback_provs = fallback_sample_profile(conn, database_id, snapshot_id, table, col, budget, e, start)
            provs.extend(fallback_provs)
            items.extend(fallback_items)
        except Exception as fallback_error:
            p = provenance(database_id, snapshot_id, "executed_sql", op, None, None, "failed", (time.monotonic() - start) * 1000, "unknown", f"exact_error={e}; fallback_error={fallback_error}")
            provs.append(p)
            items.append(context(database_id, snapshot_id, "column", "profile_failed", {"error": str(e), "fallback_error": str(fallback_error)}, op, "failed", "unknown", table, col.column_name, None, p.provenance_id))
    return items, provs


def build_context_for_database(store: ContextStore, inv, config: SDMCConfig, root: str | Path) -> None:
    db_start = time.monotonic()
    print(json.dumps({"event": "database_build_start", "dataset": inv.dataset_name, "split": inv.split_name, "database_id": inv.database_id, "sqlite_path": str(inv.sqlite_path)}, ensure_ascii=False), flush=True)
    if not inv.sqlite_exists:
        inv.build_status = "failed"
        store.upsert_inventory(inv, config.schema_version, config.sdmc_version)
        store.commit()
        print(json.dumps({"event": "database_build_done", "database_id": inv.database_id, "status": "failed_missing_sqlite", "elapsed_seconds": round(time.monotonic() - db_start, 3)}, ensure_ascii=False), flush=True)
        return
    if inv.dataset_name.lower() == "spider":
        meta = spider_schema_metadata(root).get(inv.database_id, {})
    else:
        meta = bird_schema_metadata(root, inv.split_name).get(inv.database_id, {})
    metadata_fks = dataset_metadata_fks(meta) if meta else []
    tables = extract_catalog(inv.sqlite_path, inv.database_id, metadata_fks, config.profiling.max_column_profile_seconds)
    with open_sqlite_readonly(inv.sqlite_path) as conn:
        for table in tables:
            row_sql = f"SELECT COUNT(*) AS row_count FROM {quote_ident(table.table_name)}"
            try:
                row = fetch_one(conn, row_sql, timeout_seconds=config.profiling.max_column_profile_seconds)
                table.row_count = int(row["row_count"]) if row else None
            except Exception:
                table.row_count = None
            store.insert_table(inv.database_id, inv.snapshot_id, table)
            table_ctx = {
                "table_name": table.table_name,
                "row_count": table.row_count,
                "column_count": len(table.columns),
                "primary_keys": table.primary_keys,
                "foreign_keys": [fk.__dict__ for fk in table.foreign_keys],
                "numeric_columns": [c.column_name for c in table.columns if c.inferred_profile_type == "numeric"],
                "temporal_columns": [c.column_name for c in table.columns if c.inferred_profile_type == "temporal"],
                "identifier_like_columns": [c.column_name for c in table.columns if c.is_identifier_like],
                "sensitive_like_columns": [c.column_name for c in table.columns if c.is_sensitive_like],
            }
            store.insert_context(context(inv.database_id, inv.snapshot_id, "table", "table_context", table_ctx, "aggregate_table_context", "success", "exact", table.table_name))
            for col in table.columns:
                store.insert_column(inv.database_id, inv.snapshot_id, col)
                items, provs = execute_profile(conn, inv.database_id, inv.snapshot_id, table.table_name, col, config)
                for p in provs:
                    store.insert_provenance(p)
                for item in items:
                    store.insert_context(item)
        db_ctx = {
            "table_inventory": [t.table_name for t in tables],
            "table_count": len(tables),
            "relationship_count": sum(len(t.foreign_keys) for t in tables),
        }
        item = context(inv.database_id, inv.snapshot_id, "database", "database_context", db_ctx, "aggregate_database_context", "success", "exact")
        store.insert_context(item)
    inv.build_status = "context_complete"
    store.upsert_inventory(inv, config.schema_version, config.sdmc_version)
    store.commit()
    print(json.dumps({"event": "database_build_done", "database_id": inv.database_id, "status": inv.build_status, "elapsed_seconds": round(time.monotonic() - db_start, 3)}, ensure_ascii=False), flush=True)


def run_inventory(dataset: str, split: str, root: str | Path, output_dir: str | Path, config: SDMCConfig) -> list:
    inventories = build_inventory(dataset, split, root)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    store = ContextStore(out / "context_store.sqlite")
    store.init_schema()
    for inv in inventories:
        store.upsert_inventory(inv, config.schema_version, config.sdmc_version)
    store.write_inventory_csv(inventories, out / "database_inventory.csv")
    (out / "build_manifest.json").write_text(json.dumps([{
        **{k: (str(v) if k == "sqlite_path" else v) for k, v in inv.__dict__.items()}
    } for inv in inventories], indent=2, ensure_ascii=False), encoding="utf-8")
    store.commit()
    store.close()
    return inventories


def run_build(dataset: str, split: str, root: str | Path, output_dir: str | Path, config: SDMCConfig, limit: int | None = None, force: bool = False) -> None:
    inventories = build_inventory(dataset, split, root)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    store = ContextStore(out / "context_store.sqlite")
    store.init_schema()
    for inv in inventories[:limit]:
        existing = store.conn.execute(
            "SELECT build_status FROM databases WHERE dataset_name=? AND split_name=? AND database_id=? AND snapshot_id=?",
            (inv.dataset_name, inv.split_name, inv.database_id, inv.snapshot_id),
        ).fetchone()
        if existing and existing["build_status"] in {"context_complete", "graph_complete"} and not force:
            print(json.dumps({"event": "database_build_skip", "database_id": inv.database_id, "status": existing["build_status"]}, ensure_ascii=False), flush=True)
            continue
        build_context_for_database(store, inv, config, root)
    store.close()
