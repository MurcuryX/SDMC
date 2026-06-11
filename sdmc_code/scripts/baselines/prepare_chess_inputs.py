#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def prepare_spider(data_root: Path, output_root: Path, limit: int | None) -> Path:
    source = data_root / "roots" / "spider" / "dev.json"
    db_source = data_root / "roots" / "spider" / "database"
    out_dir = output_root / "baseline_full_data"
    scratch = output_root / "baseline_scratch" / "chess_spider_dev"
    data_out = out_dir / ("chess_spider_dev.json" if limit is None else f"chess_spider_dev{limit}.json")
    db_out = scratch / "dev_databases"

    out_dir.mkdir(parents=True, exist_ok=True)
    db_out.mkdir(parents=True, exist_ok=True)

    rows = json.loads(source.read_text(encoding="utf-8"))
    if limit is not None:
        rows = rows[:limit]

    converted = []
    for idx, row in enumerate(rows):
        converted.append(
            {
                "question_id": idx,
                "db_id": row["db_id"],
                "question": row["question"],
                "evidence": "",
                "SQL": row["query"],
                "difficulty": row.get("difficulty"),
            }
        )
        target = db_out / row["db_id"]
        source_db = db_source / row["db_id"]
        if not target.exists():
            target.symlink_to(source_db, target_is_directory=True)

    data_out.write_text(json.dumps(converted, indent=2, ensure_ascii=False), encoding="utf-8")
    return data_out


def prepare_bird(data_root: Path, output_root: Path, limit: int | None) -> Path:
    source = data_root / "roots" / "bird" / "dev.json"
    if limit is None:
        return source
    out_dir = output_root / "baseline_full_data"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = json.loads(source.read_text(encoding="utf-8"))[:limit]
    data_out = out_dir / f"chess_bird_dev{limit}.json"
    data_out.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    return data_out


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare Spider/BIRD files for the CHESS baseline runner.")
    parser.add_argument("--dataset", required=True, choices=["spider", "bird"])
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    data_root = Path(args.data_root)
    output_root = Path(args.output_root)
    if args.dataset == "spider":
        path = prepare_spider(data_root, output_root, args.limit)
    else:
        path = prepare_bird(data_root, output_root, args.limit)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
