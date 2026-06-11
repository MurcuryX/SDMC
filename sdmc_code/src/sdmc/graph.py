from __future__ import annotations

from pathlib import Path
import hashlib
import json
import sqlite3
import time


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def node_id(kind: str, *parts: object) -> str:
    return ":".join([kind] + [str(p).replace(":", "_") for p in parts if p is not None])


def edge_id(kind: str, src: str, dst: str) -> str:
    h = hashlib.sha256(f"{kind}|{src}|{dst}".encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"edge:{kind}:{h}"


class GraphMaterializer:
    def __init__(self, store_path: str | Path):
        self.path = Path(store_path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    def _insert_node(self, nid: str, db: str, snapshot: str, ntype: str, label: str, ref_table=None, ref_column=None, ref_context_id=None, ref_provenance_id=None, props=None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO graph_nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (nid, db, snapshot, ntype, label, ref_table, ref_column, ref_context_id, ref_provenance_id, json.dumps(props or {}, ensure_ascii=False), now_iso()),
        )

    def _insert_edge(self, src: str, dst: str, db: str, snapshot: str, etype: str, props=None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO graph_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (edge_id(etype, src, dst), db, snapshot, src, dst, etype, json.dumps(props or {}, ensure_ascii=False), now_iso()),
        )

    def materialize_database(self, database_id: str, snapshot_id: str) -> None:
        db_node = node_id("db", database_id)
        self._insert_node(db_node, database_id, snapshot_id, "database", database_id)
        tables = self.conn.execute("SELECT * FROM tables WHERE database_id=? AND snapshot_id=?", (database_id, snapshot_id)).fetchall()
        columns = self.conn.execute("SELECT * FROM columns WHERE database_id=? AND snapshot_id=?", (database_id, snapshot_id)).fetchall()
        contexts = self.conn.execute("SELECT * FROM context_items WHERE database_id=? AND snapshot_id=?", (database_id, snapshot_id)).fetchall()
        provenances = self.conn.execute("SELECT * FROM provenance_records WHERE database_id=? AND snapshot_id=?", (database_id, snapshot_id)).fetchall()

        for table in tables:
            t_node = node_id("table", database_id, table["table_name"])
            self._insert_node(t_node, database_id, snapshot_id, "table", table["table_name"], ref_table=table["table_name"], props={"row_count": table["row_count"]})
            self._insert_edge(db_node, t_node, database_id, snapshot_id, "database_has_table")
            try:
                fks = json.loads(table["foreign_keys_json"] or "[]")
            except Exception:
                fks = []
            for fk in fks:
                src_col = node_id("column", database_id, table["table_name"], fk.get("local_column"))
                dst_col = node_id("column", database_id, fk.get("referenced_table"), fk.get("referenced_column"))
                src_table = t_node
                dst_table = node_id("table", database_id, fk.get("referenced_table"))
                self._insert_edge(src_col, dst_col, database_id, snapshot_id, "column_fk_to_column", {"fk_source": fk.get("source")})
                self._insert_edge(src_table, dst_table, database_id, snapshot_id, "table_fk_to_table", {"fk_source": fk.get("source")})

        for col in columns:
            t_node = node_id("table", database_id, col["table_name"])
            c_node = node_id("column", database_id, col["table_name"], col["column_name"])
            self._insert_node(
                c_node, database_id, snapshot_id, "column", f'{col["table_name"]}.{col["column_name"]}',
                ref_table=col["table_name"], ref_column=col["column_name"],
                props={
                    "normalized_type": col["normalized_type"],
                    "is_identifier_like": bool(col["is_identifier_like"]),
                    "is_sensitive_like": bool(col["is_sensitive_like"]),
                    "inferred_profile_type": col["inferred_profile_type"],
                },
            )
            self._insert_edge(t_node, c_node, database_id, snapshot_id, "table_has_column")

        for ctx in contexts:
            c_node = node_id("context", ctx["context_id"])
            label = f'{ctx["context_type"]}:{ctx["target_table"] or ""}.{ctx["target_column"] or ""}'
            self._insert_node(c_node, database_id, snapshot_id, "context", label, ctx["target_table"], ctx["target_column"], ctx["context_id"], ctx["provenance_id"])
            if ctx["target_column"]:
                parent = node_id("column", database_id, ctx["target_table"], ctx["target_column"])
                self._insert_edge(parent, c_node, database_id, snapshot_id, "column_has_context")
            elif ctx["target_table"]:
                parent = node_id("table", database_id, ctx["target_table"])
                self._insert_edge(parent, c_node, database_id, snapshot_id, "table_has_context")
            else:
                self._insert_edge(db_node, c_node, database_id, snapshot_id, "database_has_context")
            try:
                result = json.loads(ctx["structured_result_json"])
            except Exception:
                result = {}
            if ctx["context_type"] in {"numeric_profile", "temporal_profile", "identifier_profile"}:
                for key, val in result.items():
                    if key.endswith("_value") or key in {"mean_value", "null_ratio", "distinct_count", "uniqueness_ratio"}:
                        s_node = node_id("stat", ctx["context_id"], key)
                        self._insert_node(s_node, database_id, snapshot_id, "statistic", f"{ctx['target_table']}.{ctx['target_column']} {key}={val}", ctx["target_table"], ctx["target_column"], ctx["context_id"], ctx["provenance_id"], {"statistic": key, "value": val})
                        self._insert_edge(node_id("column", database_id, ctx["target_table"], ctx["target_column"]), s_node, database_id, snapshot_id, "column_has_statistic")
            if result.get("value_exposure_status") == "safe_observed_values":
                for value_row in result.get("top_k_values") or []:
                    value = value_row.get("value")
                    v_node = node_id("value", ctx["context_id"], str(value))
                    self._insert_node(v_node, database_id, snapshot_id, "value_encoding", f"{ctx['target_table']}.{ctx['target_column']}={value}", ctx["target_table"], ctx["target_column"], ctx["context_id"], ctx["provenance_id"], value_row)
                    self._insert_edge(node_id("column", database_id, ctx["target_table"], ctx["target_column"]), v_node, database_id, snapshot_id, "column_has_value_encoding")

        for prov in provenances:
            p_node = node_id("prov", prov["provenance_id"])
            self._insert_node(p_node, database_id, snapshot_id, "provenance", prov["source_operation"], ref_provenance_id=prov["provenance_id"], props={"source_type": prov["source_type"], "status": prov["execution_status"]})
        for ctx in contexts:
            if ctx["provenance_id"]:
                self._insert_edge(node_id("context", ctx["context_id"]), node_id("prov", ctx["provenance_id"]), database_id, snapshot_id, "context_has_provenance")

        self._write_summary(database_id, snapshot_id)
        self.conn.commit()

    def _write_summary(self, database_id: str, snapshot_id: str) -> None:
        db = self.conn.execute("SELECT dataset_name, split_name FROM databases WHERE database_id=? AND snapshot_id=? LIMIT 1", (database_id, snapshot_id)).fetchone()
        if not db:
            return
        counts = {row["node_type"]: row["n"] for row in self.conn.execute("SELECT node_type, COUNT(*) n FROM graph_nodes WHERE database_id=? AND snapshot_id=? GROUP BY node_type", (database_id, snapshot_id))}
        node_count = sum(counts.values())
        edge_count = self.conn.execute("SELECT COUNT(*) n FROM graph_edges WHERE database_id=? AND snapshot_id=?", (database_id, snapshot_id)).fetchone()["n"]
        rel_count = self.conn.execute("SELECT COUNT(*) n FROM graph_edges WHERE database_id=? AND snapshot_id=? AND edge_type IN ('table_fk_to_table','column_fk_to_column')", (database_id, snapshot_id)).fetchone()["n"]
        self.conn.execute("DELETE FROM dataset_graph_summary WHERE database_id=? AND snapshot_id=?", (database_id, snapshot_id))
        self.conn.execute(
            "INSERT INTO dataset_graph_summary VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                db["dataset_name"], db["split_name"], database_id, snapshot_id,
                node_count, edge_count, counts.get("table", 0), counts.get("column", 0),
                counts.get("context", 0), counts.get("value_encoding", 0), counts.get("statistic", 0),
                rel_count, counts.get("provenance", 0), "graph_complete",
            ),
        )
        self.conn.execute("UPDATE databases SET build_status=? WHERE database_id=? AND snapshot_id=?", ("graph_complete", database_id, snapshot_id))


def materialize_graphs(store_path: str | Path, database_id: str | None = None) -> None:
    gm = GraphMaterializer(store_path)
    try:
        if database_id:
            rows = gm.conn.execute("SELECT database_id, snapshot_id FROM databases WHERE database_id=?", (database_id,)).fetchall()
        else:
            rows = gm.conn.execute("SELECT database_id, snapshot_id FROM databases WHERE build_status IN ('context_complete','graph_complete')").fetchall()
        for row in rows:
            gm.materialize_database(row["database_id"], row["snapshot_id"])
    finally:
        gm.close()
