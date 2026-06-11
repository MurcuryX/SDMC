from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import sqlite3
import time

from sdmc.config import SDMCConfig
from sdmc.jsonl import append_jsonl
from sdmc.stage_b import DeepSeekAdapter, extract_sql


HDC_SCHEMA = """
CREATE TABLE IF NOT EXISTS hdc_contexts (
  database_id TEXT NOT NULL,
  hdc_level TEXT NOT NULL,
  target_table TEXT,
  target_column TEXT,
  context_text TEXT NOT NULL,
  model TEXT,
  prompt_hash TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (database_id, hdc_level, target_table, target_column)
);
CREATE INDEX IF NOT EXISTS idx_hdc_db ON hdc_contexts(database_id, hdc_level);
"""


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class HDCStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(HDC_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def upsert(self, database_id: str, level: str, text: str, model: str, table: str | None = None, column: str | None = None, prompt_hash: str | None = None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO hdc_contexts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (database_id, level, table, column, text, model, prompt_hash, now_iso()),
        )
        self.conn.commit()

    def fetch_for_database(self, database_id: str, limit: int = 80) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM hdc_contexts WHERE database_id=? ORDER BY hdc_level, target_table, target_column LIMIT ?",
            (database_id, limit),
        )]


def build_hdc_prompt(database_id: str, schema_text: str, level: str) -> str:
    return (
        "You generate concise hierarchical database context for Text-to-SQL.\n"
        "Use only the schema text below. Do not invent facts, values, or SQL results.\n"
        "Do not use gold SQL, evidence, examples, or question-specific information.\n"
        f"Database: {database_id}\n"
        f"Requested level: {level}\n\n"
        f"[Schema]\n{schema_text}\n\n"
        "Return compact context bullets useful for Text-to-SQL."
    )


def schema_text_from_store(context_store_path: str | Path, database_id: str) -> str:
    conn = sqlite3.connect(context_store_path)
    conn.row_factory = sqlite3.Row
    lines = []
    tables = conn.execute("SELECT table_name,row_count,primary_keys_json,foreign_keys_json FROM tables WHERE database_id=? ORDER BY table_name", (database_id,)).fetchall()
    for t in tables:
        lines.append(f"Table {t['table_name']} rows={t['row_count']} pk={t['primary_keys_json']} fk={t['foreign_keys_json']}")
        cols = conn.execute("SELECT column_name,declared_type,is_primary_key,is_foreign_key FROM columns WHERE database_id=? AND table_name=? ORDER BY ordinal_position", (database_id, t["table_name"])).fetchall()
        for c in cols:
            flags = []
            if c["is_primary_key"]:
                flags.append("PK")
            if c["is_foreign_key"]:
                flags.append("FK")
            lines.append(f"  - {c['column_name']} {c['declared_type'] or ''} {' '.join(flags)}")
    conn.close()
    return "\n".join(lines)


def generate_hdc_for_database(context_store_path: str | Path, hdc_store_path: str | Path, database_id: str, config: SDMCConfig, api_key_file: str | Path | None, allow_api_calls: bool = False) -> dict[str, Any]:
    schema_text = schema_text_from_store(context_store_path, database_id)
    adapter = DeepSeekAdapter(config, api_key_file)
    store = HDCStore(hdc_store_path)
    try:
        result = {"database_id": database_id, "levels": {}}
        for level in ["column", "table", "database"]:
            prompt = build_hdc_prompt(database_id, schema_text, level)
            gen = adapter.generate(prompt, allow_api_calls=allow_api_calls)
            text = gen.get("raw_response") or ""
            if gen.get("status") == "blocked_no_api_calls":
                text = ""
            store.upsert(database_id, level, text, config.stage_b.model)
            result["levels"][level] = {k: v for k, v in gen.items() if k != "raw_response"}
        return result
    finally:
        store.close()
