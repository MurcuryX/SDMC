from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ForeignKey:
    source_table: str | None
    local_column: str
    referenced_table: str
    referenced_column: str
    source: str


@dataclass
class ColumnMeta:
    table_name: str
    column_name: str
    ordinal_position: int
    declared_type: str | None = None
    normalized_type: str = "unknown"
    nullable: bool = True
    default_value: str | None = None
    primary_key_position: int = 0
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    is_identifier_like: bool = False
    is_sensitive_like: bool = False
    is_long_text_like: bool = False
    inferred_profile_type: str = "unknown"
    type_confidence: float = 0.0


@dataclass
class TableMeta:
    table_name: str
    columns: list[ColumnMeta] = field(default_factory=list)
    row_count: int | None = None
    primary_keys: list[str] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)


@dataclass
class DatabaseInventory:
    dataset_name: str
    split_name: str
    database_id: str
    snapshot_id: str
    sqlite_path: Path
    question_count: int = 0
    metadata_available: bool = False
    sqlite_exists: bool = False
    file_size_bytes: int = 0
    table_count: int = 0
    column_count: int = 0
    declared_pk_count: int = 0
    declared_fk_count: int = 0
    build_status: str = "pending"


@dataclass
class ContextItem:
    context_id: str
    database_id: str
    snapshot_id: str
    context_level: str
    context_type: str
    structured_result: dict[str, Any]
    source_operation: str
    execution_status: str
    exact_or_approximate: str
    target_table: str | None = None
    target_column: str | None = None
    sql_template_id: str | None = None
    provenance_id: str | None = None


@dataclass
class ProvenanceRecord:
    provenance_id: str
    database_id: str
    snapshot_id: str
    source_type: str
    source_operation: str
    execution_status: str
    exact_or_approximate: str
    sql_template_id: str | None = None
    executed_sql: str | None = None
    execution_time_ms: float | None = None
    error_message: str | None = None
