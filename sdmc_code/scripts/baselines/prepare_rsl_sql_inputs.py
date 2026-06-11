#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def ensure_link(target: Path, source: Path) -> None:
    if target.exists() or target.is_symlink():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(source, target_is_directory=source.is_dir())


def convert_spider(data_root: Path, scratch_root: Path, limit: int | None) -> tuple[Path, Path]:
    source = data_root / "roots" / "spider" / "dev.json"
    db_source = data_root / "roots" / "spider" / "database"
    out_dir = scratch_root / "rsl_sql_spider_dev"
    db_out = out_dir / "dev_databases"
    out_dir.mkdir(parents=True, exist_ok=True)
    db_out.mkdir(parents=True, exist_ok=True)

    rows = json.loads(source.read_text(encoding="utf-8"))
    if limit is not None:
        rows = rows[:limit]

    converted = []
    for row in rows:
        db_id = row["db_id"]
        converted.append(
            {
                "db_id": db_id,
                "question": row["question"],
                "evidence": row.get("evidence", ""),
                "SQL": row.get("query", row.get("SQL", "")),
                "difficulty": row.get("difficulty", ""),
            }
        )
        ensure_link(db_out / db_id, db_source / db_id)

    dev_json = out_dir / "dev.json"
    dev_json.write_text(json.dumps(converted, indent=2, ensure_ascii=False), encoding="utf-8")
    return dev_json, db_out


def convert_bird(data_root: Path, scratch_root: Path, limit: int | None) -> tuple[Path, Path]:
    source = data_root / "roots" / "bird" / "dev.json"
    db_source = data_root / "roots" / "bird" / "dev_databases"
    out_dir = scratch_root / "rsl_sql_bird_dev"
    out_dir.mkdir(parents=True, exist_ok=True)

    if limit is None:
        return source, db_source

    rows = json.loads(source.read_text(encoding="utf-8"))[:limit]
    dev_json = out_dir / f"dev{limit}.json"
    dev_json.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    return dev_json, db_source


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare Spider/BIRD inputs for RSL-SQL.")
    parser.add_argument("--dataset", required=True, choices=["spider", "bird"])
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--scratch-root", required=True)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    data_root = Path(args.data_root)
    scratch_root = Path(args.scratch_root)
    if args.dataset == "spider":
        dev_json, db_root = convert_spider(data_root, scratch_root, args.limit)
    else:
        dev_json, db_root = convert_bird(data_root, scratch_root, args.limit)

    print(json.dumps({"dev_json": str(dev_json), "db_root": str(db_root)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
