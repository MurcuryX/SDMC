from __future__ import annotations

from pathlib import Path
import argparse
import shutil
import time

from sdmc.config import load_config
from sdmc.datasets import build_inventory
from sdmc.graph import materialize_graphs
from sdmc.stage_a import build_context_for_database
from sdmc.store import ContextStore


TABLES_TO_CLEAR = [
    "tables",
    "columns",
    "context_items",
    "provenance_records",
    "graph_nodes",
    "graph_edges",
    "dataset_graph_summary",
]


def failed_database_ids(store: ContextStore) -> list[str]:
    rows = store.conn.execute(
        "SELECT DISTINCT database_id FROM context_items WHERE execution_status='failed' ORDER BY database_id"
    ).fetchall()
    return [str(r["database_id"]) for r in rows]


def clear_database_records(store: ContextStore, database_id: str, snapshot_id: str) -> None:
    for table in TABLES_TO_CLEAR:
        store.conn.execute(
            f"DELETE FROM {table} WHERE database_id=? AND snapshot_id=?",
            (database_id, snapshot_id),
        )
    store.conn.execute(
        "UPDATE databases SET build_status=? WHERE database_id=? AND snapshot_id=?",
        ("pending_targeted_rebuild", database_id, snapshot_id),
    )
    store.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="Targeted Stage A rebuild for databases with failed context rows.")
    parser.add_argument("--dataset", required=True, choices=["spider", "bird"])
    parser.add_argument("--split", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--store", required=True)
    parser.add_argument("--config", default="configs/sdmc_default.yaml")
    parser.add_argument("--database-id", action="append", help="Database id to rebuild. Defaults to all DBs with failed context rows.")
    parser.add_argument("--backup", action="store_true", help="Copy the sqlite store before modifying it.")
    args = parser.parse_args()

    config = load_config(args.config)
    store_path = Path(args.store)
    if args.backup:
        backup_path = store_path.with_suffix(f".targeted_rebuild_backup_{time.strftime('%Y%m%d_%H%M%S')}.sqlite")
        shutil.copy2(store_path, backup_path)
        print({"event": "backup_created", "path": str(backup_path)}, flush=True)

    store = ContextStore(store_path)
    store.init_schema()
    try:
        target_ids = args.database_id or failed_database_ids(store)
        if not target_ids:
            print({"event": "no_failed_databases"}, flush=True)
            return 0
        inventory = {inv.database_id: inv for inv in build_inventory(args.dataset, args.split, args.root)}
        print({"event": "targeted_rebuild_start", "database_ids": target_ids}, flush=True)
        for database_id in target_ids:
            if database_id not in inventory:
                raise ValueError(f"database_id not found in inventory: {database_id}")
            inv = inventory[database_id]
            clear_database_records(store, inv.database_id, inv.snapshot_id)
            build_context_for_database(store, inv, config, args.root)
            materialize_graphs(store_path, inv.database_id)
            failed = store.conn.execute(
                "SELECT COUNT(*) n FROM context_items WHERE database_id=? AND snapshot_id=? AND execution_status='failed'",
                (inv.database_id, inv.snapshot_id),
            ).fetchone()["n"]
            approx = store.conn.execute(
                "SELECT COUNT(*) n FROM context_items WHERE database_id=? AND snapshot_id=? AND exact_or_approximate='approximate'",
                (inv.database_id, inv.snapshot_id),
            ).fetchone()["n"]
            print({"event": "targeted_rebuild_done", "database_id": inv.database_id, "failed_context": failed, "approximate_context": approx}, flush=True)
        total_failed = store.conn.execute("SELECT COUNT(*) n FROM context_items WHERE execution_status='failed'").fetchone()["n"]
        total_approx = store.conn.execute("SELECT COUNT(*) n FROM context_items WHERE exact_or_approximate='approximate'").fetchone()["n"]
        print({"event": "targeted_rebuild_complete", "failed_context": total_failed, "approximate_context": total_approx}, flush=True)
        return 0 if total_failed == 0 else 2
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
