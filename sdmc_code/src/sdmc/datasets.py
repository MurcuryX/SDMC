from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any
import json

from sdmc.sqlite_utils import open_sqlite_readonly, fetch_all, quote_ident
from sdmc.types import DatabaseInventory, ForeignKey


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def count_questions(records: list[dict[str, Any]]) -> Counter:
    counts: Counter = Counter()
    for row in records:
        db_id = row.get("db_id") or row.get("database_id")
        if db_id:
            counts[db_id] += 1
    return counts


def build_sqlite_index(root: Path) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for path in root.rglob("*.sqlite"):
        index.setdefault(path.stem, path)
        if path.parent.name:
            index.setdefault(path.parent.name, path)
    for path in root.rglob("*.db"):
        index.setdefault(path.stem, path)
        if path.parent.name:
            index.setdefault(path.parent.name, path)
    return index


def find_sqlite_for_db(root: Path, database_id: str, sqlite_index: dict[str, Path] | None = None) -> Path | None:
    candidates = [
        root / "database" / database_id / f"{database_id}.sqlite",
        root / "database" / database_id / f"{database_id}.db",
        root / "dev_databases" / database_id / f"{database_id}.sqlite",
        root / "train_databases" / database_id / f"{database_id}.sqlite",
        root / "databases" / database_id / f"{database_id}.sqlite",
        root / database_id / f"{database_id}.sqlite",
    ]
    for c in candidates:
        if c.exists():
            return c
    if sqlite_index and database_id in sqlite_index:
        return sqlite_index[database_id]
    matches = list(root.rglob(f"{database_id}.sqlite"))
    if matches:
        return matches[0]
    matches = list(root.rglob(f"{database_id}.db"))
    return matches[0] if matches else None


def sqlite_catalog_counts(sqlite_path: Path) -> tuple[int, int, int, int]:
    with open_sqlite_readonly(sqlite_path) as conn:
        tables = [r["name"] for r in fetch_all(
            conn,
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
        )]
        column_count = 0
        pk_count = 0
        fk_count = 0
        for table in tables:
            cols = fetch_all(conn, f"PRAGMA table_info({quote_ident(table)})")
            column_count += len(cols)
            pk_count += sum(1 for c in cols if int(c["pk"] or 0) > 0)
            fk_count += len(fetch_all(conn, f"PRAGMA foreign_key_list({quote_ident(table)})"))
        return len(tables), column_count, pk_count, fk_count


def spider_schema_metadata(root: str | Path) -> dict[str, dict[str, Any]]:
    root = Path(root)
    path = root / "tables.json"
    if not path.exists():
        return {}
    data = load_json(path)
    return {row["db_id"]: row for row in data}


def bird_schema_metadata(root: str | Path, split: str) -> dict[str, dict[str, Any]]:
    root = resolve_bird_split_root(root, split)
    candidates = [
        root / f"{split}_tables.json",
        root / f"{split}" / f"{split}_tables.json",
        root / "dev_tables.json",
        root / "train_tables.json",
    ]
    for path in candidates:
        if path.exists():
            data = load_json(path)
            return {row.get("db_id") or row.get("database_id"): row for row in data}
    return {}


def resolve_bird_split_root(root: str | Path, split: str) -> Path:
    root = Path(root)
    if (root / f"{split}.json").exists() or (root / f"{split}_tables.json").exists():
        return root
    if split == "dev":
        candidates = sorted((root / "extracted").glob("dev*")) if (root / "extracted").exists() else []
        for c in candidates:
            if (c / "dev.json").exists():
                return c
    if split == "train":
        c = root / "extracted" / "train"
        if (c / "train.json").exists():
            return c
    c = root / "extracted" / split
    return c if c.exists() else root


def dataset_metadata_fks(meta: dict[str, Any]) -> list[ForeignKey]:
    table_names = meta.get("table_names_original") or meta.get("table_names") or []
    columns = meta.get("column_names_original") or meta.get("column_names") or []
    fks = meta.get("foreign_keys") or []
    out: list[ForeignKey] = []
    for pair in fks:
        if not isinstance(pair, list | tuple) or len(pair) != 2:
            continue
        a, b = int(pair[0]), int(pair[1])
        if a >= len(columns) or b >= len(columns):
            continue
        src_table_idx, src_col = columns[a]
        dst_table_idx, dst_col = columns[b]
        if src_table_idx < 0 or dst_table_idx < 0:
            continue
        out.append(ForeignKey(
            source_table=str(table_names[src_table_idx]),
            local_column=str(src_col),
            referenced_table=str(table_names[dst_table_idx]),
            referenced_column=str(dst_col),
            source="dataset_metadata",
        ))
    return out


def build_inventory(dataset_name: str, split_name: str, root: str | Path) -> list[DatabaseInventory]:
    root = Path(root)
    if dataset_name.lower() == "spider":
        question_files = {
            "train": ["train_spider.json", "train_others.json"],
            "dev": ["dev.json"],
            "test": ["test.json"],
        }.get(split_name, [f"{split_name}.json"])
        schema_meta = spider_schema_metadata(root)
    elif dataset_name.lower() == "bird":
        root = resolve_bird_split_root(root, split_name)
        question_files = [f"{split_name}.json", f"{split_name}/{split_name}.json"]
        schema_meta = bird_schema_metadata(root, split_name)
    else:
        raise ValueError(f"unsupported dataset: {dataset_name}")

    questions: list[dict[str, Any]] = []
    for name in question_files:
        path = root / name
        if path.exists():
            loaded = load_json(path)
            if isinstance(loaded, list):
                questions.extend(loaded)
    q_counts = count_questions(questions)
    db_ids = set(q_counts) | set(schema_meta)
    sqlite_index = build_sqlite_index(root)
    inventories: list[DatabaseInventory] = []
    for db_id in sorted(db_ids):
        sqlite_path = find_sqlite_for_db(root, db_id, sqlite_index)
        sqlite_exists = sqlite_path is not None and sqlite_path.exists()
        file_size = sqlite_path.stat().st_size if sqlite_exists and sqlite_path else 0
        table_count = column_count = pk_count = fk_count = 0
        if sqlite_path and sqlite_exists:
            try:
                table_count, column_count, pk_count, fk_count = sqlite_catalog_counts(sqlite_path)
            except Exception:
                pass
        inv = DatabaseInventory(
            dataset_name=dataset_name,
            split_name=split_name,
            database_id=db_id,
            snapshot_id=f"{dataset_name}_{split_name}_{db_id}",
            sqlite_path=sqlite_path or Path(""),
            question_count=q_counts[db_id],
            metadata_available=db_id in schema_meta,
            sqlite_exists=sqlite_exists,
            file_size_bytes=file_size,
            table_count=table_count,
            column_count=column_count,
            declared_pk_count=pk_count,
            declared_fk_count=fk_count,
        )
        inventories.append(inv)
    return inventories
