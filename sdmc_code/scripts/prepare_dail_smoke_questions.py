#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def schema_text(db_path: Path) -> str:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        lines = []
        for (table,) in rows:
            cols = conn.execute(f"PRAGMA table_info(`{table}`)").fetchall()
            col_text = ", ".join(f"{c[1]} {c[2]}".strip() for c in cols)
            lines.append(f"Table {table}({col_text})")
        return "\n".join(lines)
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare minimal DAIL-SQL-compatible questions.json for smoke tests.")
    parser.add_argument("--dataset", required=True, choices=["spider", "bird"])
    parser.add_argument("--input", required=True)
    parser.add_argument("--db-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()

    rows = json.loads(Path(args.input).read_text(encoding="utf-8"))[: args.limit]
    questions = []
    db_root = Path(args.db_root)
    for row in rows:
        db_id = row["db_id"]
        db_path = db_root / db_id / f"{db_id}.sqlite"
        prompt = (
            "Generate exactly one SQLite SQL query. Return SQL only.\n\n"
            f"[Schema]\n{schema_text(db_path)}\n\n"
            f"[Question]\n{row['question']}\n"
        )
        if row.get("evidence"):
            prompt += f"\n[Evidence]\n{row['evidence']}\n"
        questions.append({"prompt": prompt, "db_id": db_id})
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "questions.json").write_text(json.dumps({"questions": questions}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "ok", "output": str(out / "questions.json"), "rows": len(questions)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
