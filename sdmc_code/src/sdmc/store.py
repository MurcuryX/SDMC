from __future__ import annotations

from pathlib import Path
from typing import Iterable
import csv
import json
import sqlite3
import time

from sdmc.types import ColumnMeta, ContextItem, DatabaseInventory, ProvenanceRecord, TableMeta


STORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS databases (
  database_id TEXT NOT NULL,
  dataset_name TEXT NOT NULL,
  split_name TEXT NOT NULL,
  snapshot_id TEXT NOT NULL,
  sqlite_path TEXT NOT NULL,
  sqlite_path_hash TEXT,
  file_size_bytes INTEGER,
  table_count INTEGER,
  column_count INTEGER,
  question_count INTEGER,
  build_status TEXT NOT NULL,
  schema_version TEXT,
  sdmc_version TEXT,
  config_hash TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (dataset_name, split_name, database_id, snapshot_id)
);
CREATE TABLE IF NOT EXISTS tables (
  table_id TEXT PRIMARY KEY,
  database_id TEXT NOT NULL,
  snapshot_id TEXT NOT NULL,
  table_name TEXT NOT NULL,
  row_count INTEGER,
  column_count INTEGER,
  primary_keys_json TEXT,
  foreign_keys_json TEXT,
  build_status TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS columns (
  column_id TEXT PRIMARY KEY,
  database_id TEXT NOT NULL,
  snapshot_id TEXT NOT NULL,
  table_name TEXT NOT NULL,
  column_name TEXT NOT NULL,
  ordinal_position INTEGER,
  declared_type TEXT,
  normalized_type TEXT,
  nullable INTEGER,
  is_primary_key INTEGER,
  is_foreign_key INTEGER,
  foreign_key_target_json TEXT,
  is_identifier_like INTEGER,
  is_sensitive_like INTEGER,
  is_long_text_like INTEGER,
  inferred_profile_type TEXT,
  type_confidence REAL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS context_items (
  context_id TEXT PRIMARY KEY,
  database_id TEXT NOT NULL,
  snapshot_id TEXT NOT NULL,
  context_level TEXT NOT NULL,
  context_type TEXT NOT NULL,
  target_table TEXT,
  target_column TEXT,
  structured_result_json TEXT NOT NULL,
  source_operation TEXT NOT NULL,
  sql_template_id TEXT,
  provenance_id TEXT,
  execution_status TEXT NOT NULL,
  exact_or_approximate TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS provenance_records (
  provenance_id TEXT PRIMARY KEY,
  database_id TEXT NOT NULL,
  snapshot_id TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_operation TEXT NOT NULL,
  sql_template_id TEXT,
  executed_sql TEXT,
  execution_status TEXT NOT NULL,
  execution_time_ms REAL,
  exact_or_approximate TEXT NOT NULL,
  error_message TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS build_runs (
  build_run_id TEXT PRIMARY KEY,
  dataset_name TEXT NOT NULL,
  split_name TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  git_commit TEXT,
  config_json TEXT,
  status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS graph_nodes (
  node_id TEXT PRIMARY KEY,
  database_id TEXT NOT NULL,
  snapshot_id TEXT NOT NULL,
  node_type TEXT NOT NULL,
  label TEXT NOT NULL,
  ref_table TEXT,
  ref_column TEXT,
  ref_context_id TEXT,
  ref_provenance_id TEXT,
  properties_json TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS graph_edges (
  edge_id TEXT PRIMARY KEY,
  database_id TEXT NOT NULL,
  snapshot_id TEXT NOT NULL,
  source_node_id TEXT NOT NULL,
  target_node_id TEXT NOT NULL,
  edge_type TEXT NOT NULL,
  properties_json TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dataset_graph_summary (
  dataset_name TEXT,
  split_name TEXT,
  database_id TEXT,
  snapshot_id TEXT,
  node_count INTEGER,
  edge_count INTEGER,
  table_node_count INTEGER,
  column_node_count INTEGER,
  context_node_count INTEGER,
  value_encoding_node_count INTEGER,
  statistic_node_count INTEGER,
  relationship_edge_count INTEGER,
  provenance_node_count INTEGER,
  build_status TEXT
);
CREATE INDEX IF NOT EXISTS idx_context_db ON context_items(database_id, snapshot_id);
CREATE INDEX IF NOT EXISTS idx_context_target ON context_items(database_id, target_table, target_column);
CREATE INDEX IF NOT EXISTS idx_context_type ON context_items(database_id, context_level, context_type);
CREATE INDEX IF NOT EXISTS idx_columns_lookup ON columns(database_id, table_name, column_name);
CREATE INDEX IF NOT EXISTS idx_provenance_db ON provenance_records(database_id, snapshot_id);
CREATE INDEX IF NOT EXISTS idx_graph_node_db_type ON graph_nodes(database_id, node_type);
CREATE INDEX IF NOT EXISTS idx_graph_node_label ON graph_nodes(database_id, label);
CREATE INDEX IF NOT EXISTS idx_graph_edge_source ON graph_edges(database_id, source_node_id);
CREATE INDEX IF NOT EXISTS idx_graph_edge_target ON graph_edges(database_id, target_node_id);
CREATE INDEX IF NOT EXISTS idx_graph_edge_type ON graph_edges(database_id, edge_type);
"""


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class ContextStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        self.conn.executescript(STORE_SCHEMA)
        self.conn.commit()

    def upsert_inventory(self, inv: DatabaseInventory, schema_version: str, sdmc_version: str, config_hash: str | None = None) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO databases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                inv.database_id, inv.dataset_name, inv.split_name, inv.snapshot_id,
                str(inv.sqlite_path), None, inv.file_size_bytes, inv.table_count,
                inv.column_count, inv.question_count, inv.build_status, schema_version,
                sdmc_version, config_hash, now_iso(),
            ),
        )

    def write_inventory_csv(self, inventories: Iterable[DatabaseInventory], path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        rows = list(inventories)
        with p.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(DatabaseInventory.__dataclass_fields__.keys()))
            writer.writeheader()
            for row in rows:
                data = row.__dict__.copy()
                data["sqlite_path"] = str(data["sqlite_path"])
                writer.writerow(data)

    def insert_table(self, database_id: str, snapshot_id: str, table: TableMeta) -> None:
        table_id = f"table:{database_id}:{table.table_name}"
        self.conn.execute(
            "INSERT OR REPLACE INTO tables VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                table_id, database_id, snapshot_id, table.table_name, table.row_count,
                len(table.columns), json.dumps(table.primary_keys, ensure_ascii=False),
                json.dumps([fk.__dict__ for fk in table.foreign_keys], ensure_ascii=False),
                "complete", now_iso(),
            ),
        )

    def insert_column(self, database_id: str, snapshot_id: str, col: ColumnMeta) -> None:
        column_id = f"column:{database_id}:{col.table_name}:{col.column_name}"
        self.conn.execute(
            "INSERT OR REPLACE INTO columns VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                column_id, database_id, snapshot_id, col.table_name, col.column_name,
                col.ordinal_position, col.declared_type, col.normalized_type,
                1 if col.nullable else 0, 1 if col.primary_key_position else 0,
                1 if col.foreign_keys else 0,
                json.dumps([fk.__dict__ for fk in col.foreign_keys], ensure_ascii=False),
                1 if col.is_identifier_like else 0,
                1 if col.is_sensitive_like else 0,
                1 if col.is_long_text_like else 0,
                col.inferred_profile_type, col.type_confidence, now_iso(),
            ),
        )

    def insert_context(self, item: ContextItem) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO context_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item.context_id, item.database_id, item.snapshot_id, item.context_level,
                item.context_type, item.target_table, item.target_column,
                json.dumps(item.structured_result, ensure_ascii=False),
                item.source_operation, item.sql_template_id, item.provenance_id,
                item.execution_status, item.exact_or_approximate, now_iso(),
            ),
        )

    def insert_provenance(self, prov: ProvenanceRecord) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO provenance_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                prov.provenance_id, prov.database_id, prov.snapshot_id, prov.source_type,
                prov.source_operation, prov.sql_template_id, prov.executed_sql,
                prov.execution_status, prov.execution_time_ms, prov.exact_or_approximate,
                prov.error_message, now_iso(),
            ),
        )

    def commit(self) -> None:
        self.conn.commit()
