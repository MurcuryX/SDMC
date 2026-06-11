from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request
import json
import math
import re
import sqlite3
import time

from sdmc.config import SDMCConfig, read_api_key
from sdmc.sqlite_utils import open_sqlite_readonly, fetch_all


TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|'[^']*'|\"[^\"]*\"")


def normalize_text(text: str) -> list[str]:
    pieces: list[str] = []
    for token in TOKEN_RE.findall(text):
        token = token.strip("'\"").lower()
        token = token.replace("_", " ")
        for part in token.split():
            if part.endswith("s") and len(part) > 3:
                pieces.append(part[:-1])
            pieces.append(part)
    return [p for p in pieces if p]


def question_representation(question: str) -> dict[str, Any]:
    tokens = normalize_text(question)
    numbers = [t for t in tokens if re.fullmatch(r"\d+(?:\.\d+)?", t)]
    ops = []
    text = question.lower()
    if any(w in text for w in ["after", "greater than", "more than", "above", "since"]):
        ops.append("greater_than_or_after")
    if any(w in text for w in ["before", "less than", "below", "earlier"]):
        ops.append("less_than_or_before")
    if any(w in text for w in ["average", "avg", "mean"]):
        ops.append("average")
    if any(w in text for w in ["maximum", "highest", "max", "largest"]):
        ops.append("max")
    if any(w in text for w in ["minimum", "lowest", "min", "smallest"]):
        ops.append("min")
    if any(w in text for w in ["count", "number of", "how many"]):
        ops.append("count")
    return {"tokens": tokens, "numeric_literals": numbers, "operator_hints": ops, "intent_hints": []}


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.docs = docs
        self.k1 = k1
        self.b = b
        self.avgdl = sum(len(d) for d in docs) / len(docs) if docs else 0.0
        self.df: dict[str, int] = {}
        for doc in docs:
            for tok in set(doc):
                self.df[tok] = self.df.get(tok, 0) + 1
        self.n = len(docs)

    def score(self, query: list[str], doc: list[str]) -> float:
        if not doc:
            return 0.0
        counts: dict[str, int] = {}
        for tok in doc:
            counts[tok] = counts.get(tok, 0) + 1
        score = 0.0
        for tok in query:
            if tok not in counts:
                continue
            df = self.df.get(tok, 0)
            idf = math.log(1 + (self.n - df + 0.5) / (df + 0.5))
            tf = counts[tok]
            denom = tf + self.k1 * (1 - self.b + self.b * len(doc) / (self.avgdl or 1))
            score += idf * tf * (self.k1 + 1) / denom
        return score


@dataclass
class SchemaSelection:
    tables: list[str]
    columns: list[tuple[str, str]]
    relationships: list[dict[str, Any]]
    trace: dict[str, Any]


class StageBEngine:
    def __init__(self, store_path: str | Path, config: SDMCConfig):
        self.store_path = Path(store_path)
        self.config = config
        self.conn = sqlite3.connect(self.store_path)
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    def shared_schema_selector(self, database_id: str, question: str) -> SchemaSelection:
        q = question_representation(question)
        q_tokens = q["tokens"]
        rows = self.conn.execute("SELECT table_name, column_name, normalized_type, is_primary_key, is_foreign_key FROM columns WHERE database_id=?", (database_id,)).fetchall()
        docs = [normalize_text(f"{r['table_name']} {r['column_name']} {r['normalized_type']}") for r in rows]
        bm25 = BM25(docs)
        content_boost: dict[tuple[str, str], float] = {}
        for vr in self.conn.execute(
            "SELECT label,ref_table,ref_column FROM graph_nodes WHERE database_id=? AND node_type='value_encoding'",
            (database_id,),
        ):
            toks = set(normalize_text(vr["label"] or ""))
            overlap = len(set(q_tokens) & toks)
            if overlap and vr["ref_table"] and vr["ref_column"]:
                content_boost[(vr["ref_table"], vr["ref_column"])] = content_boost.get((vr["ref_table"], vr["ref_column"]), 0.0) + 4.0 * overlap
        for cr in self.conn.execute(
            """
            SELECT target_table,target_column,context_type,structured_result_json
            FROM context_items
            WHERE database_id=? AND execution_status='success' AND target_table IS NOT NULL
            """,
            (database_id,),
        ):
            target_col = cr["target_column"]
            if not target_col:
                continue
            search_text = f"{cr['context_type']} {cr['target_table']} {target_col} "
            try:
                payload = json.loads(cr["structured_result_json"] or "{}")
            except Exception:
                payload = {}
            for key in ("top_k_values", "min_value", "max_value", "earliest_value", "latest_value", "value_exposure_status"):
                if key in payload:
                    search_text += f" {key} {payload[key]}"
            overlap = len(set(q_tokens) & set(normalize_text(search_text)))
            if overlap:
                pair = (cr["target_table"], target_col)
                content_boost[pair] = content_boost.get(pair, 0.0) + 1.5 * overlap
        scored = []
        for r, doc in zip(rows, docs):
            lexical = len(set(q_tokens) & set(doc)) * 2.0
            exact = 5.0 if r["column_name"].lower() in question.lower() or r["table_name"].lower() in question.lower() else 0.0
            bm = bm25.score(q_tokens, doc) if self.config.stage_b.use_bm25 else 0.0
            score = lexical + exact + bm + content_boost.get((r["table_name"], r["column_name"]), 0.0)
            if score > 0:
                scored.append((score, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        selected_cols: list[tuple[str, str]] = []
        selected_tables: list[str] = []
        for _, r in scored:
            table = r["table_name"]
            col = r["column_name"]
            if table not in selected_tables and len(selected_tables) < self.config.stage_b.max_selected_tables:
                selected_tables.append(table)
            if len(selected_cols) < self.config.stage_b.max_selected_columns:
                selected_cols.append((table, col))
        # Include PK/FK columns for selected tables.
        for table in list(selected_tables):
            extras = self.conn.execute("SELECT table_name, column_name FROM columns WHERE database_id=? AND table_name=? AND (is_primary_key=1 OR is_foreign_key=1)", (database_id, table)).fetchall()
            for e in extras:
                pair = (e["table_name"], e["column_name"])
                if pair not in selected_cols:
                    selected_cols.append(pair)
        selected_table_set = set(selected_tables)
        rels = []
        for r in self.conn.execute(
            """
            SELECT source_node_id, target_node_id, edge_type, properties_json
            FROM graph_edges
            WHERE database_id=? AND edge_type IN ('table_fk_to_table','column_fk_to_column')
            """,
            (database_id,),
        ):
            src = self._node_to_path(r["source_node_id"])
            tgt = self._node_to_path(r["target_node_id"])
            src_table = src.split(".", 1)[0]
            tgt_table = tgt.split(".", 1)[0]
            if src_table in selected_table_set or tgt_table in selected_table_set:
                rels.append(dict(r))
        return SchemaSelection(selected_tables, selected_cols[:self.config.stage_b.max_selected_columns], rels, {"question": q, "scored_count": len(scored), "use_bm25": self.config.stage_b.use_bm25})

    def full_schema_selection(self, database_id: str) -> SchemaSelection:
        rows = self.conn.execute(
            "SELECT table_name, column_name FROM columns WHERE database_id=? ORDER BY table_name, column_name",
            (database_id,),
        ).fetchall()
        tables: list[str] = []
        columns: list[tuple[str, str]] = []
        for r in rows:
            table = r["table_name"]
            if table not in tables:
                tables.append(table)
            columns.append((table, r["column_name"]))
        rels = [dict(r) for r in self.conn.execute(
            """
            SELECT source_node_id, target_node_id, edge_type, properties_json
            FROM graph_edges
            WHERE database_id=? AND edge_type IN ('table_fk_to_table','column_fk_to_column')
            """,
            (database_id,),
        )]
        return SchemaSelection(
            tables[: self.config.stage_b.max_selected_tables],
            columns[: self.config.stage_b.max_selected_columns],
            rels,
            {"question": question_representation(""), "scored_count": len(columns), "use_bm25": False, "mode": "full_schema"},
        )

    def retrieve_sdmc_candidates(self, database_id: str, question: str, schema: SchemaSelection) -> list[tuple[float, sqlite3.Row]]:
        q = question_representation(question)
        q_tokens = q["tokens"]
        node_rows = self.conn.execute("SELECT * FROM graph_nodes WHERE database_id=?", (database_id,)).fetchall()
        docs = [normalize_text(self._node_search_text(r)) for r in node_rows]
        bm25 = BM25(docs)
        schema_tables = set(schema.tables)
        schema_cols = set(schema.columns)
        scored: list[tuple[float, sqlite3.Row]] = []
        for row, doc in zip(node_rows, docs):
            score = len(set(q_tokens) & set(doc)) * 2.0
            score += bm25.score(q_tokens, doc) if self.config.stage_b.use_bm25 else 0.0
            if row["ref_table"] in schema_tables:
                score += 1.5
            if (row["ref_table"], row["ref_column"]) in schema_cols:
                score += 2.0
            if row["node_type"] == "value_encoding" and score > 0:
                score += 6.0
            if row["node_type"] == "statistic" and any(h in q["operator_hints"] for h in ["average", "max", "min", "count", "greater_than_or_after", "less_than_or_before"]):
                score += 3.0
            try:
                props = json.loads(row["properties_json"] or "{}")
            except Exception:
                props = {}
            if props.get("is_sensitive_like"):
                score -= 10.0
            if score > 0:
                scored.append((score, row))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored

    def _node_search_text(self, row: sqlite3.Row) -> str:
        text = row["label"] or ""
        context_id = row["ref_context_id"]
        if not context_id:
            return text
        item = self._context_item(context_id)
        if not item:
            return text
        text += f" {item['context_level']} {item['context_type']} {item['target_table'] or ''} {item['target_column'] or ''}"
        try:
            payload = json.loads(item["structured_result_json"] or "{}")
        except Exception:
            payload = {}
        for key in (
            "row_count",
            "distinct_count",
            "null_ratio",
            "min_value",
            "max_value",
            "mean_value",
            "earliest_value",
            "latest_value",
            "top_k_values",
            "value_exposure_status",
            "suppression_reason",
        ):
            if key in payload:
                text += f" {key} {payload[key]}"
        return text

    def expand_and_select_subgraph(self, database_id: str, question: str, schema: SchemaSelection) -> dict[str, Any]:
        candidates = self.retrieve_sdmc_candidates(database_id, question, schema)
        selected_nodes: dict[str, sqlite3.Row] = {}
        selected_edges: dict[str, sqlite3.Row] = {}
        selection_reasons: dict[str, list[str]] = {}
        included_context_ids: set[str] = set()
        included_provenance_ids: set[str] = set()

        def add_node(row: sqlite3.Row, reason: str) -> None:
            if row["node_id"] not in selected_nodes:
                selected_nodes[row["node_id"]] = row
            selection_reasons.setdefault(row["node_id"], [])
            if reason not in selection_reasons[row["node_id"]]:
                selection_reasons[row["node_id"]].append(reason)
            if row["ref_context_id"]:
                included_context_ids.add(row["ref_context_id"])
            if row["ref_provenance_id"]:
                included_provenance_ids.add(row["ref_provenance_id"])

        # Mandatory schema nodes.
        for table in schema.tables:
            row = self.conn.execute("SELECT * FROM graph_nodes WHERE database_id=? AND node_type='table' AND ref_table=? LIMIT 1", (database_id, table)).fetchone()
            if row:
                add_node(row, "mandatory_schema_table")
        for table, col in schema.columns:
            row = self.conn.execute("SELECT * FROM graph_nodes WHERE database_id=? AND node_type='column' AND ref_table=? AND ref_column=? LIMIT 1", (database_id, table, col)).fetchone()
            if row:
                add_node(row, "mandatory_schema_column")

        max_nodes = self.config.stage_b.max_context_items + len(selected_nodes)
        for _, row in candidates:
            if len(selected_nodes) >= max_nodes:
                break
            add_node(row, "candidate_score")
            for edge in self.conn.execute("SELECT * FROM graph_edges WHERE database_id=? AND (source_node_id=? OR target_node_id=?) LIMIT 20", (database_id, row["node_id"], row["node_id"])):
                selected_edges[edge["edge_id"]] = edge
                other_id = edge["target_node_id"] if edge["source_node_id"] == row["node_id"] else edge["source_node_id"]
                other = self.conn.execute("SELECT * FROM graph_nodes WHERE node_id=?", (other_id,)).fetchone()
                if other and len(selected_nodes) < max_nodes:
                    add_node(other, f"expanded_{edge['edge_type']}")

        return {
            "database_id": database_id,
            "question": question,
            "selected_nodes": [dict(r) for r in selected_nodes.values()],
            "selected_edges": [dict(r) for r in selected_edges.values()],
            "included_context_ids": sorted(included_context_ids),
            "included_provenance_ids": sorted(included_provenance_ids),
            "selection_reasons": selection_reasons,
            "budget_summary": {"selected_node_count": len(selected_nodes), "selected_edge_count": len(selected_edges), "max_nodes": max_nodes},
            "truncation_flag": len(candidates) > max_nodes,
        }

    @staticmethod
    def _node_to_path(node_id: str) -> str:
        parts = node_id.split(":")
        if len(parts) >= 4 and parts[0] == "column":
            return f"{parts[2]}.{parts[3]}"
        if len(parts) >= 3 and parts[0] == "table":
            return parts[2]
        return node_id

    @staticmethod
    def _brief_context_payload(context_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        priority = [
            "row_count",
            "column_count",
            "primary_key",
            "foreign_key_count",
            "distinct_count",
            "null_ratio",
            "min_value",
            "max_value",
            "mean_value",
            "earliest_value",
            "latest_value",
            "top_k_values",
            "value_exposure_status",
            "suppression_reason",
            "table_role",
            "database_domain",
        ]
        brief = {k: payload[k] for k in priority if k in payload}
        if brief:
            return brief
        if context_type in {"table_context", "database_context"}:
            return {k: payload[k] for k in list(payload)[:8]}
        return {k: payload[k] for k in list(payload)[:5]}

    def _context_item(self, context_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT context_level,context_type,target_table,target_column,structured_result_json FROM context_items WHERE context_id=? LIMIT 1",
            (context_id,),
        ).fetchone()

    def render_context(self, database_id: str, schema: SchemaSelection, subgraph: dict[str, Any] | None, condition: str) -> str:
        lines: list[str] = ["[Selected Schema]"]
        by_table: dict[str, list[str]] = {}
        col_rows = self.conn.execute("SELECT * FROM columns WHERE database_id=?", (database_id,)).fetchall()
        col_map = {(r["table_name"], r["column_name"]): r for r in col_rows}
        for table, col in schema.columns:
            by_table.setdefault(table, []).append(col)
        for table in schema.tables:
            lines.append(f"Table: {table}")
            for col in by_table.get(table, []):
                row = col_map.get((table, col))
                suffix = ""
                if row:
                    if row["is_primary_key"]:
                        suffix += " PRIMARY KEY"
                    if row["is_foreign_key"]:
                        suffix += " FOREIGN KEY"
                    if row["inferred_profile_type"]:
                        suffix += f" {row['inferred_profile_type']}"
                lines.append(f"  - {col}{suffix}")
        if condition == "C0":
            return "\n".join(lines)
        if condition in {"C1", "SDMC"}:
            lines.append("\n[Join and Relationship Hints]")
            for rel in schema.relationships[: self.config.stage_b.max_relationship_edges]:
                lines.append(f"  - {self._node_to_path(rel['source_node_id'])} -> {self._node_to_path(rel['target_node_id'])}")
        if condition == "C1":
            lines.append("\n[Safe Observed Values]")
            selected_pairs = set(schema.columns)
            value_rows = self.conn.execute(
                "SELECT label,ref_table,ref_column FROM graph_nodes WHERE database_id=? AND node_type='value_encoding'",
                (database_id,),
            ).fetchall()
            shown = 0
            for r in value_rows:
                if (r["ref_table"], r["ref_column"]) not in selected_pairs:
                    continue
                lines.append(f"  - {r['label']}")
                shown += 1
                if shown >= self.config.stage_b.max_value_encoding_nodes:
                    break
            return "\n".join(lines)
        if subgraph:
            lines.append("\n[SDMC Selected Context]")
            value_count = stat_count = 0
            context_count = 0
            suppressed: list[str] = []
            for node in subgraph["selected_nodes"]:
                ntype = node["node_type"]
                if ntype == "value_encoding" and value_count < self.config.stage_b.max_value_encoding_nodes:
                    lines.append(f"Value encoding: {node['label']}")
                    value_count += 1
                elif ntype == "statistic" and stat_count < self.config.stage_b.max_statistic_nodes:
                    lines.append(f"Statistic: {node['label']}")
                    stat_count += 1
                elif ntype == "context" and context_count < self.config.stage_b.max_context_items:
                    try:
                        props = json.loads(node["properties_json"] or "{}")
                    except Exception:
                        props = {}
                    if props.get("value_exposure_status") == "suppressed":
                        suppressed.append(node["label"])
                    context_id = node.get("ref_context_id")
                    if context_id:
                        item = self._context_item(context_id)
                        if item:
                            try:
                                payload = json.loads(item["structured_result_json"] or "{}")
                            except Exception:
                                payload = {}
                            brief = self._brief_context_payload(item["context_type"], payload)
                            target = item["target_table"] or "DATABASE"
                            if item["target_column"]:
                                target = f"{target}.{item['target_column']}"
                            lines.append(
                                f"{item['context_level']} {item['context_type']} {target}: "
                                f"{json.dumps(brief, ensure_ascii=False)[:500]}"
                            )
                            context_count += 1
            if suppressed:
                lines.append("\n[Suppressed Values]")
                for item in suppressed[:20]:
                    lines.append(f"  - {item}")
            lines.append("\n[SQL Generation Notes]")
            lines.append("Use only SELECT or WITH. Do not invent tables or columns. Use declared or metadata-backed relationships for joins.")
        return "\n".join(lines)

    def build_prompt(self, question: str, rendered_context: str) -> str:
        style = self.config.stage_b.prompt_style
        if style == "sql_decision":
            return (
                "You are a rigorous Text-to-SQL system. Generate exactly one SQLite SQL query.\n"
                "Internally decide the relevant tables, join path, filters, aggregations, ordering, and limits before writing SQL.\n"
                "Use the supplied context as evidence, but do not invent tables, columns, values, or relationships.\n"
                "Return only SQL, with no explanation, no markdown, and no intermediate reasoning.\n\n"
                f"{rendered_context}\n\n"
                f"[Question]\n{question}\n"
            )
        if style == "sdmc_verified":
            return (
                "You are a rigorous Text-to-SQL system. Generate exactly one SQLite SQL query.\n"
                "Follow this private checklist before finalizing the SQL: "
                "(1) choose only schema-visible tables and columns; "
                "(2) use SDMC context only as supporting evidence; "
                "(3) verify join keys against relationship hints; "
                "(4) map value mentions to observed values only when supported; "
                "(5) ensure the SQL is executable in SQLite.\n"
                "Return only the final SQL, with no explanation, no markdown, and no checklist.\n\n"
                f"{rendered_context}\n\n"
                f"[Question]\n{question}\n"
            )
        return (
            "You are a Text-to-SQL system. Generate exactly one SQLite SQL query.\n"
            "Return only SQL, with no explanation.\n\n"
            f"{rendered_context}\n\n"
            f"[Question]\n{question}\n"
        )


def leakage_flags(prompt: str, gold_sql: str | None = None, bird_evidence: str | None = None) -> list[str]:
    flags = []
    lowered = prompt.lower()
    if gold_sql and gold_sql.strip() and gold_sql.strip().lower() in lowered:
        flags.append("gold_sql_leak")
    if bird_evidence and bird_evidence.strip() and bird_evidence.strip().lower() in lowered:
        flags.append("bird_evidence_leak")
    if "api_key" in lowered or "sk-" in lowered:
        flags.append("secret_leak")
    return flags


def extract_sql(text: str) -> str | None:
    fenced = re.search(r"```sql\s*(.*?)```", text, re.I | re.S)
    if fenced:
        text = fenced.group(1)
    match = re.search(r"\b(SELECT|WITH)\b.*?(?:;|$)", text, re.I | re.S)
    if not match:
        return None
    sql = match.group(0).strip().rstrip(";").strip()
    return sql if sql.lower().startswith(("select", "with")) else None


def _normalize_result_value(value: Any) -> tuple[str, Any]:
    if value is None:
        return ("null", None)
    if isinstance(value, bytes):
        return ("bytes", value.hex())
    if isinstance(value, int):
        return ("number", float(value))
    if isinstance(value, float):
        return ("number", round(value, 8))
    return ("text", str(value))


def _result_signature(rows: list[tuple[Any, ...]]) -> list[str]:
    encoded = [
        json.dumps([_normalize_result_value(v) for v in row], ensure_ascii=False, sort_keys=True)
        for row in rows
    ]
    return sorted(encoded)


def evaluate_readonly(sqlite_path: str | Path, predicted_sql: str, gold_sql: str) -> dict[str, Any]:
    start = time.monotonic()
    try:
        if not predicted_sql.lower().lstrip().startswith(("select", "with")):
            return {"execution_status": "not_executed", "execution_match": False, "error_category": "invalid_sql"}
        with open_sqlite_readonly(sqlite_path, timeout_seconds=30) as conn:
            pred = [tuple(r) for r in fetch_all(conn, predicted_sql, timeout_seconds=30)]
            gold = [tuple(r) for r in fetch_all(conn, gold_sql, timeout_seconds=30)]
        pred_sig = _result_signature(pred)
        gold_sig = _result_signature(gold)
        matched = pred_sig == gold_sig
        return {
            "execution_status": "success",
            "execution_match": matched,
            "result_preview": repr(pred[:5]),
            "latency": time.monotonic() - start,
            "error_category": None if matched else "wrong_execution_result",
            "predicted_row_count": len(pred),
            "gold_row_count": len(gold),
        }
    except Exception as e:
        message = str(e)
        category = "timeout_or_interrupted" if "interrupted" in message.lower() else "runtime_error"
        return {"execution_status": "runtime_error", "execution_match": False, "latency": time.monotonic() - start, "error_category": category, "error": message}


def explain_readonly(sqlite_path: str | Path, predicted_sql: str) -> dict[str, Any]:
    start = time.monotonic()
    try:
        if not predicted_sql.lower().lstrip().startswith(("select", "with")):
            return {
                "explain_status": "invalid_sql",
                "ok": False,
                "latency": time.monotonic() - start,
                "error": "only SELECT/WITH SQL can be explained",
            }
        with open_sqlite_readonly(sqlite_path, timeout_seconds=10) as conn:
            rows = fetch_all(conn, f"EXPLAIN QUERY PLAN {predicted_sql}", timeout_seconds=10)
        return {
            "explain_status": "success",
            "ok": True,
            "latency": time.monotonic() - start,
            "plan_preview": [tuple(r) for r in rows[:5]],
        }
    except Exception as e:
        return {
            "explain_status": "error",
            "ok": False,
            "latency": time.monotonic() - start,
            "error": str(e),
        }


class DeepSeekAdapter:
    def __init__(self, config: SDMCConfig, api_key_file: str | Path | None = None):
        self.config = config
        self.api_key_file = api_key_file

    def generate(self, prompt: str, allow_api_calls: bool = False) -> dict[str, Any]:
        if not allow_api_calls:
            return {"raw_response": "", "generated_sql": None, "status": "blocked_no_api_calls"}
        key = read_api_key(self.api_key_file)
        if not key:
            return {"raw_response": "", "generated_sql": None, "status": "missing_api_key"}
        payload = {
            "model": self.config.stage_b.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.config.stage_b.temperature,
            "max_tokens": self.config.stage_b.max_output_tokens,
            "stream": False,
        }
        if self.config.stage_b.thinking in {"enabled", "disabled"}:
            payload["thinking"] = {"type": self.config.stage_b.thinking}
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.config.stage_b.endpoint.rstrip('/')}/chat/completions",
            data=data,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            method="POST",
        )
        start = time.monotonic()
        last_error = ""
        for attempt in range(3):
            try:
                with request.urlopen(req, timeout=120) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                content = body["choices"][0]["message"]["content"]
                return {
                    "raw_response": content,
                    "generated_sql": extract_sql(content),
                    "status": "success",
                    "latency": time.monotonic() - start,
                    "usage": body.get("usage", {}),
                    "attempts": attempt + 1,
                }
            except urlerror.HTTPError as e:
                last_error = f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:500]}"
                if e.code not in {429, 500, 502, 503, 504}:
                    break
            except (urlerror.URLError, TimeoutError) as e:
                last_error = str(e)
            if attempt < 2:
                time.sleep(2 ** attempt)
        return {"raw_response": "", "generated_sql": None, "status": "api_error", "latency": time.monotonic() - start, "error": last_error, "usage": {}}


def build_repair_prompt(original_prompt: str, generated_sql: str, error_message: str) -> str:
    return (
        "You are repairing a SQLite SQL query for a Text-to-SQL task.\n"
        "Use only the schema and context in the original prompt. Do not invent tables, columns, values, or relationships.\n"
        "Fix the SQL so it executes in SQLite and still answers the original question.\n"
        "Return only the repaired SQL, with no explanation and no markdown.\n\n"
        "[Original Prompt]\n"
        f"{original_prompt}\n\n"
        "[SQL to Repair]\n"
        f"{generated_sql}\n\n"
        "[Execution Error]\n"
        f"{error_message[:1000]}\n"
    )


def dry_run_question(store_path: str | Path, database_id: str, question: str, config: SDMCConfig) -> dict[str, Any]:
    engine = StageBEngine(store_path, config)
    try:
        schema = engine.shared_schema_selector(database_id, question)
        subgraph = engine.expand_and_select_subgraph(database_id, question, schema)
        outputs = {}
        for condition in ["C0", "C1", "SDMC"]:
            rendered = engine.render_context(database_id, schema, subgraph if condition == "SDMC" else None, condition)
            prompt = engine.build_prompt(question, rendered)
            outputs[condition] = {
                "rendered_context": rendered,
                "prompt": prompt,
                "estimated_token_count": max(1, len(prompt) // 4),
                "leakage_flags": leakage_flags(prompt),
            }
        return {"schema": schema.__dict__, "selected_subgraph": subgraph, "conditions": outputs}
    finally:
        engine.close()
