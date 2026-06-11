from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import csv
import json
import sqlite3
import time

from sdmc.config import SDMCConfig
from sdmc.hdc import HDCStore
from sdmc.jsonl import append_jsonl, read_jsonl
from sdmc.questions import QuestionExample, load_questions, question_to_json
from sdmc.stage_b import DeepSeekAdapter, StageBEngine, build_repair_prompt, evaluate_readonly, explain_readonly, leakage_flags, normalize_text
from sdmc.stage_b import SchemaSelection


RAW_SCHEMA = "RAW_SCHEMA"
C1 = "C1"
HDC_STYLE = "HDC_STYLE"
SDMC = "SDMC"
SDMC_FULL = "SDMC_FULL"
SDMC_FLAT_STORE = "SDMC_FLAT_STORE"
SDMC_GRAPH_NO_REL = "SDMC_GRAPH_NO_REL"
SDMC_GRAPH_SCHEMA_ONLY = "SDMC_GRAPH_SCHEMA_ONLY"
SDMC_NO_VALUE = "SDMC_NO_VALUE"
SDMC_NO_STATS = "SDMC_NO_STATS"
SDMC_NO_REL = "SDMC_NO_REL"
SDMC_NO_TABLE_DB = "SDMC_NO_TABLE_DB"
SDMC_NO_COLUMN_CTX = "SDMC_NO_COLUMN_CTX"
SDMC_NO_TABLE_CTX = "SDMC_NO_TABLE_CTX"
SDMC_NO_DATABASE_CTX = "SDMC_NO_DATABASE_CTX"
SDMC_ONLY_COLUMN_CTX = "SDMC_ONLY_COLUMN_CTX"
SDMC_ONLY_TABLE_CTX = "SDMC_ONLY_TABLE_CTX"
SDMC_ONLY_DATABASE_CTX = "SDMC_ONLY_DATABASE_CTX"


@dataclass(frozen=True)
class ExperimentSpec:
    dataset: str
    split: str
    root: str
    store: str
    output_dir: str
    conditions: list[str]
    limit: int | None = None
    sample: int | None = None
    seed: int = 13
    api_key_file: str | None = None
    hdc_store: str | None = None


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def query_features(gold_sql: str | None) -> dict[str, Any]:
    sql = (gold_sql or "").lower()
    if not sql:
        return {
            "has_gold_sql": False,
            "has_join": None,
            "has_aggregation": None,
            "has_group_by": None,
            "has_order_by": None,
            "has_nested_query": None,
            "has_set_operation": None,
            "estimated_complexity": "unknown",
        }
    has_join = " join " in f" {sql} " or sql.count(" from ") > 1
    has_agg = any(f"{fn}(" in sql for fn in ["count", "sum", "avg", "min", "max"])
    has_group = " group by " in sql
    has_order = " order by " in sql
    has_nested = sql.count("select") > 1
    has_set = any(f" {op} " in f" {sql} " for op in ["union", "intersect", "except"])
    score = sum(bool(x) for x in [has_join, has_agg, has_group, has_order, has_nested, has_set])
    complexity = "easy" if score <= 1 else "medium" if score <= 3 else "hard"
    return {
        "has_gold_sql": True,
        "has_join": has_join,
        "has_aggregation": has_agg,
        "has_group_by": has_group,
        "has_order_by": has_order,
        "has_nested_query": has_nested,
        "has_set_operation": has_set,
        "estimated_complexity": complexity,
    }


def enforce_prompt_budget(engine: StageBEngine, question: str, rendered_context: str, budget_tokens: int) -> tuple[str, str, dict[str, Any]]:
    prompt = engine.build_prompt(question, rendered_context)
    estimated = estimate_tokens(prompt)
    if estimated <= budget_tokens:
        return prompt, rendered_context, {
            "budget_tokens": budget_tokens,
            "estimated_input_tokens_before": estimated,
            "estimated_input_tokens_after": estimated,
            "applied": False,
        }
    overhead = estimate_tokens(engine.build_prompt(question, ""))
    allowed_context_tokens = max(256, budget_tokens - overhead - 32)
    allowed_chars = allowed_context_tokens * 4
    marker = "\n\n[Context Truncated]\nThe selected context exceeded the configured prompt budget; lower-ranked trailing context was omitted."
    truncated_context = rendered_context[:allowed_chars].rstrip() + marker
    prompt = engine.build_prompt(question, truncated_context)
    return prompt, truncated_context, {
        "budget_tokens": budget_tokens,
        "estimated_input_tokens_before": estimated,
        "estimated_input_tokens_after": estimate_tokens(prompt),
        "applied": True,
        "allowed_context_tokens": allowed_context_tokens,
    }


def _brief_hdc_payload(row: dict[str, Any], max_chars: int = 520) -> str:
    level = str(row.get("hdc_level") or "")
    table = row.get("target_table")
    column = row.get("target_column")
    raw = str(row.get("context_text") or "").strip()
    prefix = f"[{level}]"
    if table and column:
        prefix += f" {table}.{column}"
    elif table:
        prefix += f" {table}"

    try:
        payload = json.loads(raw)
    except Exception:
        text = raw
    else:
        if level == "column":
            parts = []
            for key in ("semantic_type", "possible_meaning", "summary"):
                if payload.get(key):
                    parts.append(f"{key}={payload[key]}")
            samples = payload.get("samples")
            if isinstance(samples, list) and samples:
                parts.append("samples=" + json.dumps(samples[:3], ensure_ascii=False))
            text = "; ".join(parts) or json.dumps(payload, ensure_ascii=False, sort_keys=True)
        elif level == "table":
            parts = []
            for key in ("entity", "description", "table_type"):
                if payload.get(key):
                    parts.append(f"{key}={payload[key]}")
            rels = payload.get("relationships")
            if isinstance(rels, list) and rels:
                parts.append("relationships=" + json.dumps(rels[:2], ensure_ascii=False))
            text = "; ".join(parts) or json.dumps(payload, ensure_ascii=False, sort_keys=True)
        elif level == "database":
            parts = []
            for key in ("domain", "summary", "business_impact"):
                if payload.get(key):
                    parts.append(f"{key}={payload[key]}")
            entities = payload.get("entities")
            if isinstance(entities, list) and entities:
                entity_names = [e.get("name") for e in entities if isinstance(e, dict) and e.get("name")]
                if entity_names:
                    parts.append("entities=" + json.dumps(entity_names[:10], ensure_ascii=False))
            text = "; ".join(parts) or json.dumps(payload, ensure_ascii=False, sort_keys=True)
        else:
            text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    text = " ".join(text.split())
    if len(text) > max_chars:
        text = text[: max_chars - 18].rstrip() + " ... [trimmed]"
    return f"{prefix}: {text}"


def render_hdc_compact(engine: StageBEngine, question: QuestionExample, schema: SchemaSelection, hdc_store: HDCStore, config: SDMCConfig) -> tuple[str, dict[str, Any]]:
    rows = hdc_store.fetch_for_database(question.database_id, limit=10000)
    if not rows:
        return "(HDC context missing.)", {"hdc_rows_total": 0, "hdc_rows_selected": 0, "mode": "compact"}

    q_tokens = set(normalize_text(question.question + " " + (question.evidence or "")))
    selected_tables = set(schema.tables)
    selected_columns = set(schema.columns)
    level_priority = {"database": 3, "table": 2, "column": 1}
    scored: list[tuple[float, int, dict[str, Any], str]] = []
    for raw_row in rows:
        row = dict(raw_row)
        level = str(row.get("hdc_level") or "")
        table = row.get("target_table")
        column = row.get("target_column")
        brief = _brief_hdc_payload(row)
        haystack = " ".join([str(table or ""), str(column or ""), brief])
        overlap = len(q_tokens & set(normalize_text(haystack)))
        score = float(overlap * 3)
        if level == "database":
            score += 2.0
        if table in selected_tables:
            score += 10.0 if level == "table" else 5.0
        if table and column and (table, column) in selected_columns:
            score += 14.0
        if level == "column" and table in selected_tables:
            score += 2.0
        scored.append((score, level_priority.get(level, 0), row, brief))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)

    # Keep one database summary when available, then fill the budget with the
    # most question/schema-relevant table and column HDC facts.
    chosen: list[tuple[float, dict[str, Any], str]] = []
    seen_keys: set[tuple[str, str | None, str | None]] = set()
    for score, _, row, brief in scored:
        level = str(row.get("hdc_level") or "")
        key = (level, row.get("target_table"), row.get("target_column"))
        if key in seen_keys:
            continue
        if level == "database" or score > 0:
            chosen.append((score, row, brief))
            seen_keys.add(key)
        if len(chosen) >= config.stage_b.max_context_items:
            break

    base_schema = engine.render_context(question.database_id, schema, None, "C0")
    header = "[HDC-style Context]\nMode: compact question-time selection over LLM-generated hierarchical context.\n"
    hdc_lines: list[str] = []
    omitted = 0
    for score, row, brief in chosen:
        candidate_lines = hdc_lines + [brief]
        rendered = header + "\n".join(candidate_lines) + "\n\n" + base_schema
        prompt = engine.build_prompt(question.question, rendered)
        if estimate_tokens(prompt) > min(config.stage_b.prompt_budget_tokens - 512, 7000):
            omitted += 1
            continue
        hdc_lines = candidate_lines
    rendered_hdc = "\n".join(hdc_lines) if hdc_lines else "(No HDC rows fit the compact prompt budget.)"
    trace = {
        "mode": "compact",
        "hdc_rows_total": len(rows),
        "hdc_rows_candidate": len(chosen),
        "hdc_rows_selected": len(hdc_lines),
        "hdc_rows_omitted_by_budget": omitted + max(0, len(chosen) - len(hdc_lines) - omitted),
        "selected_tables": sorted(selected_tables),
        "selected_columns": [f"{t}.{c}" for t, c in schema.columns],
    }
    return rendered_hdc + "\n\n" + base_schema, trace


def completed_pairs(output_dir: str | Path) -> set[tuple[str, str]]:
    rows = read_jsonl(Path(output_dir) / "executions.jsonl")
    return {(str(r.get("question_id")), str(r.get("condition_id"))) for r in rows if r.get("execution_status") not in {None, "not_run"}}


def sqlite_path_for_db(context_store: str | Path, database_id: str) -> str | None:
    conn = sqlite3.connect(context_store)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT sqlite_path FROM databases WHERE database_id=? LIMIT 1", (database_id,)).fetchone()
    conn.close()
    return row["sqlite_path"] if row else None


def filtered_subgraph(subgraph: dict[str, Any], *, node_types_to_drop: set[str] | None = None, edge_predicate=None, drop_expanded_only_nodes: bool = False) -> dict[str, Any]:
    node_types_to_drop = node_types_to_drop or set()
    original_reasons = subgraph.get("selection_reasons", {}) or {}
    nodes = []
    for n in subgraph.get("selected_nodes", []):
        if n.get("node_type") in node_types_to_drop:
            continue
        reasons = original_reasons.get(n.get("node_id"), [])
        if drop_expanded_only_nodes and reasons and all(str(r).startswith("expanded_") for r in reasons):
            continue
        nodes.append(n)
    node_ids = {n.get("node_id") for n in nodes}
    if edge_predicate is None:
        edges = [e for e in subgraph.get("selected_edges", []) if e.get("source_node_id") in node_ids and e.get("target_node_id") in node_ids]
    else:
        edges = [
            e for e in subgraph.get("selected_edges", [])
            if e.get("source_node_id") in node_ids and e.get("target_node_id") in node_ids and edge_predicate(e)
        ]
    context_ids = sorted({n.get("ref_context_id") for n in nodes if n.get("ref_context_id")})
    provenance_ids = sorted({n.get("ref_provenance_id") for n in nodes if n.get("ref_provenance_id")})
    reasons = {nid: subgraph.get("selection_reasons", {}).get(nid, []) for nid in node_ids if nid}
    return {
        **subgraph,
        "selected_nodes": nodes,
        "selected_edges": edges,
        "included_context_ids": context_ids,
        "included_provenance_ids": provenance_ids,
        "selection_reasons": reasons,
        "budget_summary": {
            **(subgraph.get("budget_summary") or {}),
            "selected_node_count": len(nodes),
            "selected_edge_count": len(edges),
        },
    }


def filter_subgraph_by_context_levels(subgraph: dict[str, Any], *, drop_levels: set[str] | None = None, keep_levels: set[str] | None = None) -> dict[str, Any]:
    drop_levels = drop_levels or set()
    context_nodes = [n for n in subgraph.get("selected_nodes", []) if n.get("node_type") == "context" and n.get("ref_context_id")]
    if not context_nodes:
        return subgraph
    context_ids = sorted({n.get("ref_context_id") for n in context_nodes if n.get("ref_context_id")})
    # The level map is filled later by condition_prompt through StageBEngine.
    return {
        **subgraph,
        "_context_level_filter": {
            "drop_levels": sorted(drop_levels),
            "keep_levels": sorted(keep_levels) if keep_levels is not None else None,
            "context_ids": context_ids,
        },
    }


def apply_context_level_filter(engine: StageBEngine, subgraph: dict[str, Any]) -> dict[str, Any]:
    filt = subgraph.get("_context_level_filter")
    if not filt:
        return subgraph
    context_ids = filt.get("context_ids") or []
    if not context_ids:
        return subgraph
    placeholders = ",".join("?" for _ in context_ids)
    rows = engine.conn.execute(
        f"SELECT context_id, context_level FROM context_items WHERE context_id IN ({placeholders})",
        context_ids,
    ).fetchall()
    levels = {r["context_id"]: r["context_level"] for r in rows}
    drop_levels = set(filt.get("drop_levels") or [])
    keep_levels = set(filt.get("keep_levels")) if filt.get("keep_levels") is not None else None

    nodes = []
    for node in subgraph.get("selected_nodes", []):
        cid = node.get("ref_context_id")
        level = levels.get(cid)
        if level:
            if keep_levels is not None and level not in keep_levels:
                continue
            if level in drop_levels:
                continue
        nodes.append(node)
    node_ids = {n.get("node_id") for n in nodes}
    edges = [
        e for e in subgraph.get("selected_edges", [])
        if e.get("source_node_id") in node_ids and e.get("target_node_id") in node_ids
    ]
    return {
        **subgraph,
        "selected_nodes": nodes,
        "selected_edges": edges,
        "included_context_ids": sorted({n.get("ref_context_id") for n in nodes if n.get("ref_context_id")}),
        "included_provenance_ids": sorted({n.get("ref_provenance_id") for n in nodes if n.get("ref_provenance_id")}),
        "selection_reasons": {nid: subgraph.get("selection_reasons", {}).get(nid, []) for nid in node_ids if nid},
        "budget_summary": {
            **(subgraph.get("budget_summary") or {}),
            "selected_node_count": len(nodes),
            "selected_edge_count": len(edges),
        },
    }


def condition_prompt(engine: StageBEngine, question: QuestionExample, condition: str, config: SDMCConfig, hdc_store: HDCStore | None = None) -> dict[str, Any]:
    t0 = time.monotonic()
    schema = engine.shared_schema_selector(question.database_id, question.question)
    subgraph = engine.expand_and_select_subgraph(question.database_id, question.question, schema)
    effective_subgraph: dict[str, Any] = {"selected_nodes": [], "selected_edges": [], "included_context_ids": [], "included_provenance_ids": [], "selection_reasons": {}}
    rendered = ""
    condition_warnings: list[str] = []
    selection_record: dict[str, Any] = {
        "question_id": question.question_id,
        "condition_id": condition,
        "database_id": question.database_id,
        "schema_tables": schema.tables,
        "schema_columns": schema.columns,
        "schema_trace": schema.trace,
    }

    if condition == RAW_SCHEMA:
        rendered = engine.render_context(question.database_id, schema, None, "C0")
    elif condition == C1:
        rendered = engine.render_context(question.database_id, schema, None, "C1")
    elif condition == HDC_STYLE:
        if hdc_store is None:
            condition_warnings.append("missing_hdc_store")
            rendered = "[HDC-style Context]\n(HDC context store unavailable.)\n\n" + engine.render_context(question.database_id, schema, None, "C0")
        else:
            hdc_text, hdc_trace = render_hdc_compact(engine, question, schema, hdc_store, config)
            selection_record["hdc_compact_trace"] = hdc_trace
            if hdc_trace.get("hdc_rows_total", 0) == 0:
                condition_warnings.append("missing_hdc_context")
            rendered = "[HDC-style Context]\n" + hdc_text
    elif condition == SDMC:
        rendered = engine.render_context(question.database_id, schema, subgraph, "SDMC")
        effective_subgraph = subgraph
    elif condition == SDMC_GRAPH_NO_REL or condition == SDMC_NO_REL:
        subgraph = filtered_subgraph(subgraph, edge_predicate=lambda e: False, drop_expanded_only_nodes=True)
        no_rel_schema = SchemaSelection(schema.tables, schema.columns, [], {**schema.trace, "relationships_disabled": True})
        rendered = engine.render_context(question.database_id, no_rel_schema, subgraph, "SDMC")
        rendered = rendered.replace("[Join and Relationship Hints]", "[Join and Relationship Hints Disabled]")
        effective_subgraph = subgraph
    elif condition == SDMC_GRAPH_SCHEMA_ONLY:
        subgraph = filtered_subgraph(subgraph, node_types_to_drop={"database", "context", "value_encoding", "statistic", "provenance"})
        rendered = engine.render_context(question.database_id, schema, subgraph, "SDMC")
        rendered = rendered.replace("[SDMC Selected Context]", "[Schema Graph Only]")
        effective_subgraph = subgraph
    elif condition == SDMC_NO_VALUE:
        subgraph = filtered_subgraph(subgraph, node_types_to_drop={"value_encoding"})
        rendered = engine.render_context(question.database_id, schema, subgraph, "SDMC")
        effective_subgraph = subgraph
    elif condition == SDMC_NO_STATS:
        subgraph = filtered_subgraph(subgraph, node_types_to_drop={"statistic"})
        rendered = engine.render_context(question.database_id, schema, subgraph, "SDMC")
        effective_subgraph = subgraph
    elif condition == SDMC_NO_TABLE_DB:
        subgraph = filtered_subgraph(subgraph, node_types_to_drop={"table", "database"})
        rendered = engine.render_context(question.database_id, schema, subgraph, "SDMC")
        effective_subgraph = subgraph
    elif condition == SDMC_NO_COLUMN_CTX:
        subgraph = apply_context_level_filter(engine, filter_subgraph_by_context_levels(subgraph, drop_levels={"column"}))
        rendered = engine.render_context(question.database_id, schema, subgraph, "SDMC")
        effective_subgraph = subgraph
    elif condition == SDMC_NO_TABLE_CTX:
        subgraph = apply_context_level_filter(engine, filter_subgraph_by_context_levels(subgraph, drop_levels={"table"}))
        rendered = engine.render_context(question.database_id, schema, subgraph, "SDMC")
        effective_subgraph = subgraph
    elif condition == SDMC_NO_DATABASE_CTX:
        subgraph = apply_context_level_filter(engine, filter_subgraph_by_context_levels(subgraph, drop_levels={"database"}))
        rendered = engine.render_context(question.database_id, schema, subgraph, "SDMC")
        effective_subgraph = subgraph
    elif condition == SDMC_ONLY_COLUMN_CTX:
        subgraph = apply_context_level_filter(engine, filter_subgraph_by_context_levels(subgraph, keep_levels={"column"}))
        rendered = engine.render_context(question.database_id, schema, subgraph, "SDMC")
        effective_subgraph = subgraph
    elif condition == SDMC_ONLY_TABLE_CTX:
        subgraph = apply_context_level_filter(engine, filter_subgraph_by_context_levels(subgraph, keep_levels={"table"}))
        rendered = engine.render_context(question.database_id, schema, subgraph, "SDMC")
        effective_subgraph = subgraph
    elif condition == SDMC_ONLY_DATABASE_CTX:
        subgraph = apply_context_level_filter(engine, filter_subgraph_by_context_levels(subgraph, keep_levels={"database"}))
        rendered = engine.render_context(question.database_id, schema, subgraph, "SDMC")
        effective_subgraph = subgraph
    elif condition == SDMC_FLAT_STORE:
        rendered = render_flat_store(engine, question, schema, config)
    elif condition == SDMC_FULL:
        rendered = render_full_context(engine, question.database_id, config)
    else:
        raise ValueError(f"unknown condition: {condition}")

    prompt, rendered, budget_trace = enforce_prompt_budget(engine, question.question, rendered, config.stage_b.prompt_budget_tokens)
    flags = leakage_flags(prompt, gold_sql=question.gold_sql, bird_evidence=question.evidence)
    selection_record.update({
        "selected_node_count": len(effective_subgraph.get("selected_nodes", [])),
        "selected_edge_count": len(effective_subgraph.get("selected_edges", [])),
        "included_context_count": len(effective_subgraph.get("included_context_ids", [])),
        "included_provenance_count": len(effective_subgraph.get("included_provenance_ids", [])),
        "selected_node_ids": [n.get("node_id") for n in effective_subgraph.get("selected_nodes", [])],
        "selected_edge_ids": [e.get("edge_id") for e in effective_subgraph.get("selected_edges", [])],
        "selection_reasons": effective_subgraph.get("selection_reasons", {}),
        "condition_warnings": condition_warnings,
        "candidate_truncation_flag": bool(effective_subgraph.get("selected_nodes")) and subgraph.get("truncation_flag", False),
        "prompt_truncation_flag": budget_trace["applied"],
        "prompt_budget_trace": budget_trace,
        "render_seconds": time.monotonic() - t0,
    })
    return {
        "prompt": prompt,
        "rendered_context": rendered,
        "selection_record": selection_record,
        "selected_subgraph": subgraph,
        "leakage_flags": flags,
        "condition_warnings": condition_warnings,
        "prompt_budget_trace": budget_trace,
        "estimated_input_tokens": estimate_tokens(prompt),
    }


def render_flat_store(engine: StageBEngine, question: QuestionExample, schema, config: SDMCConfig) -> str:
    q_tokens = set(question.question.lower().replace("_", " ").split())
    rows = engine.conn.execute(
        "SELECT context_type,target_table,target_column,structured_result_json FROM context_items WHERE database_id=? AND execution_status='success'",
        (question.database_id,),
    ).fetchall()
    scored = []
    for r in rows:
        text = f"{r['context_type']} {r['target_table']} {r['target_column']}".lower()
        score = sum(1 for t in q_tokens if t and t in text)
        if score > 0:
            scored.append((score, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    lines = [engine.render_context(question.database_id, schema, None, "C0"), "\n[Flat Context Store Facts]"]
    for _, r in scored[: config.stage_b.max_context_items]:
        result = json.loads(r["structured_result_json"])
        brief = {k: v for k, v in result.items() if k in {"row_count", "distinct_count", "null_ratio", "min_value", "max_value", "mean_value", "earliest_value", "latest_value", "value_exposure_status", "top_k_values"}}
        lines.append(f"- {r['context_type']} {r['target_table']}.{r['target_column']}: {json.dumps(brief, ensure_ascii=False)[:500]}")
    return "\n".join(lines)


def render_full_context(engine: StageBEngine, database_id: str, config: SDMCConfig) -> str:
    schema = engine.full_schema_selection(database_id)
    lines = [engine.render_context(database_id, schema, None, "C0"), "\n[Full Offline Context]"]
    rows = engine.conn.execute(
        "SELECT context_type,target_table,target_column,structured_result_json FROM context_items WHERE database_id=? AND execution_status='success' LIMIT ?",
        (database_id, config.stage_b.max_context_items * 3),
    ).fetchall()
    for r in rows:
        result = json.loads(r["structured_result_json"])
        brief = {k: v for k, v in result.items() if k in {"row_count", "distinct_count", "null_ratio", "min_value", "max_value", "mean_value", "earliest_value", "latest_value", "value_exposure_status", "top_k_values"}}
        lines.append(f"- {r['context_type']} {r['target_table']}.{r['target_column']}: {json.dumps(brief, ensure_ascii=False)[:500]}")
    return "\n".join(lines)


def run_experiment(spec: ExperimentSpec, config: SDMCConfig, allow_api_calls: bool = False, dry_run: bool = True) -> dict[str, Any]:
    out = Path(spec.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    questions = load_questions(spec.dataset, spec.split, spec.root, limit=spec.limit, sample=spec.sample, seed=spec.seed)
    engine = StageBEngine(spec.store, config)
    adapter = DeepSeekAdapter(config, spec.api_key_file)
    hdc_store = HDCStore(spec.hdc_store) if spec.hdc_store else None
    completed = completed_pairs(out)
    planned_pairs = [(q.question_id, c) for q in questions for c in spec.conditions if (str(q.question_id), c) not in completed]
    if allow_api_calls and not dry_run and len(planned_pairs) > config.stage_b.max_api_calls_per_run:
        raise RuntimeError(f"planned API calls {len(planned_pairs)} exceed max_api_calls_per_run={config.stage_b.max_api_calls_per_run}")
    run_meta = {
        "dataset": spec.dataset,
        "split": spec.split,
        "conditions": spec.conditions,
        "question_count": len(questions),
        "dry_run": dry_run,
        "allow_api_calls": allow_api_calls,
        "model": config.stage_b.model,
        "temperature": config.stage_b.temperature,
        "max_output_tokens": config.stage_b.max_output_tokens,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (out / "run_config.json").write_text(json.dumps({**run_meta, "spec": asdict(spec)}, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        for q in questions:
            q_features = query_features(q.gold_sql)
            sqlite_path = sqlite_path_for_db(spec.store, q.database_id)
            for condition in spec.conditions:
                key = (str(q.question_id), condition)
                if key in completed:
                    continue
                prompt_pack = condition_prompt(engine, q, condition, config, hdc_store)
                prompt_record = {
                    "question_id": q.question_id,
                    "condition_id": condition,
                    "database_id": q.database_id,
                    "question": q.question,
                    "query_features": q_features,
                    "prompt": prompt_pack["prompt"],
                    "rendered_context": prompt_pack["rendered_context"],
                    "estimated_input_tokens": prompt_pack["estimated_input_tokens"],
                    "prompt_budget_trace": prompt_pack["prompt_budget_trace"],
                    "condition_warnings": prompt_pack["condition_warnings"],
                    "leakage_flags": prompt_pack["leakage_flags"],
                }
                append_jsonl(out / "prompt_records.jsonl", prompt_record)
                append_jsonl(out / "selection_records.jsonl", prompt_pack["selection_record"])
                if prompt_pack["leakage_flags"]:
                    pred = {"question_id": q.question_id, "condition_id": condition, "status": "blocked_leakage", "generated_sql": None}
                    exe = {"question_id": q.question_id, "condition_id": condition, "execution_status": "prompt_leakage_detected", "execution_match": False}
                elif condition == HDC_STYLE and prompt_pack["condition_warnings"] and not dry_run:
                    pred = {"question_id": q.question_id, "condition_id": condition, "status": "blocked_missing_hdc_context", "generated_sql": None}
                    exe = {"question_id": q.question_id, "condition_id": condition, "execution_status": "missing_hdc_context", "execution_match": False}
                elif prompt_pack["estimated_input_tokens"] > config.stage_b.max_prompt_tokens_for_api:
                    pred = {"question_id": q.question_id, "condition_id": condition, "status": "blocked_prompt_budget", "generated_sql": None}
                    exe = {"question_id": q.question_id, "condition_id": condition, "execution_status": "prompt_budget_exceeded", "execution_match": False}
                elif dry_run:
                    pred = {"question_id": q.question_id, "condition_id": condition, "status": "dry_run", "generated_sql": None}
                    exe = {"question_id": q.question_id, "condition_id": condition, "execution_status": "not_run", "execution_match": None}
                else:
                    gen = adapter.generate(prompt_pack["prompt"], allow_api_calls=allow_api_calls)
                    repair_attempts = 0
                    repair_sources: list[str] = []
                    original_generated_sql = gen.get("generated_sql")
                    original_status = gen.get("status")
                    explain_check: dict[str, Any] | None = None
                    pred = {
                        "question_id": q.question_id,
                        "condition_id": condition,
                        "database_id": q.database_id,
                        "status": gen.get("status"),
                        "generated_sql": gen.get("generated_sql"),
                        "original_generated_sql": original_generated_sql,
                        "repair_attempts": repair_attempts,
                        "repair_sources": repair_sources,
                        "explain_status": None,
                        "explain_error": None,
                        "generation_latency_seconds": gen.get("latency"),
                        "usage": gen.get("usage", {}),
                        "raw_response": gen.get("raw_response", ""),
                    }
                    if gen.get("generated_sql") and q.gold_sql and sqlite_path:
                        if config.stage_b.enable_explain_repair:
                            explain_check = explain_readonly(sqlite_path, gen["generated_sql"])
                            if (
                                not explain_check.get("ok")
                                and repair_attempts < config.stage_b.max_repair_attempts
                            ):
                                repair_prompt = build_repair_prompt(
                                    prompt_pack["prompt"],
                                    gen["generated_sql"],
                                    f"EXPLAIN QUERY PLAN error: {explain_check.get('error') or explain_check.get('explain_status')}",
                                )
                                repair = adapter.generate(repair_prompt, allow_api_calls=allow_api_calls)
                                repair_attempts += 1
                                repair_sources.append("explain")
                                if repair.get("generated_sql"):
                                    repaired_explain = explain_readonly(sqlite_path, repair["generated_sql"])
                                    gen = repair
                                    explain_check = repaired_explain
                        exe = evaluate_readonly(sqlite_path, gen["generated_sql"], q.gold_sql)
                        while (
                            config.stage_b.enable_runtime_repair
                            and repair_attempts < config.stage_b.max_repair_attempts
                            and exe.get("execution_status") == "runtime_error"
                        ):
                            repair_prompt = build_repair_prompt(prompt_pack["prompt"], gen["generated_sql"], str(exe.get("error") or ""))
                            repair = adapter.generate(repair_prompt, allow_api_calls=allow_api_calls)
                            repair_attempts += 1
                            repair_sources.append("runtime")
                            if not repair.get("generated_sql"):
                                break
                            repaired_exe = evaluate_readonly(sqlite_path, repair["generated_sql"], q.gold_sql)
                            if repaired_exe.get("execution_status") != "runtime_error":
                                gen = repair
                                exe = repaired_exe
                                break
                            gen = repair
                            exe = repaired_exe
                        pred.update({
                            "status": gen.get("status", original_status),
                            "generated_sql": gen.get("generated_sql"),
                            "repair_attempts": repair_attempts,
                            "repair_sources": repair_sources,
                            "explain_status": (explain_check or {}).get("explain_status"),
                            "explain_error": (explain_check or {}).get("error"),
                            "raw_response": gen.get("raw_response", pred.get("raw_response", "")),
                        })
                        exe.update({"question_id": q.question_id, "condition_id": condition, "database_id": q.database_id})
                    else:
                        exe = {"question_id": q.question_id, "condition_id": condition, "database_id": q.database_id, "execution_status": "not_evaluated", "execution_match": False}
                append_jsonl(out / "predictions.jsonl", pred)
                append_jsonl(out / "executions.jsonl", exe)
                completed.add(key)
        write_per_question_results(out)
        return {"status": "ok", "output_dir": str(out), "questions": len(questions), "conditions": spec.conditions}
    finally:
        engine.close()
        if hdc_store:
            hdc_store.close()


def write_per_question_results(out: Path) -> None:
    prompts = read_jsonl(out / "prompt_records.jsonl")
    preds = read_jsonl(out / "predictions.jsonl")
    execs = read_jsonl(out / "executions.jsonl")
    by_pred = {(str(r.get("question_id")), r.get("condition_id")): r for r in preds}
    by_exec = {(str(r.get("question_id")), r.get("condition_id")): r for r in execs}
    rows = []
    for p in prompts:
        key = (str(p.get("question_id")), p.get("condition_id"))
        pred = by_pred.get(key, {})
        exe = by_exec.get(key, {})
        rows.append({
            "question_id": p.get("question_id"),
            "database_id": p.get("database_id"),
            "condition_id": p.get("condition_id"),
            "estimated_complexity": (p.get("query_features") or {}).get("estimated_complexity"),
            "has_join": (p.get("query_features") or {}).get("has_join"),
            "has_aggregation": (p.get("query_features") or {}).get("has_aggregation"),
            "has_nested_query": (p.get("query_features") or {}).get("has_nested_query"),
            "estimated_input_tokens": p.get("estimated_input_tokens"),
            "prompt_truncated": (p.get("prompt_budget_trace") or {}).get("applied"),
            "condition_warnings": ";".join(p.get("condition_warnings") or []),
            "leakage_flags": ";".join(p.get("leakage_flags") or []),
            "prediction_status": pred.get("status"),
            "generated_sql": pred.get("generated_sql"),
            "repair_attempts": pred.get("repair_attempts"),
            "repair_sources": ";".join(pred.get("repair_sources") or []),
            "explain_status": pred.get("explain_status"),
            "explain_error": pred.get("explain_error"),
            "execution_status": exe.get("execution_status"),
            "execution_match": exe.get("execution_match"),
            "error_category": exe.get("error_category"),
            "predicted_row_count": exe.get("predicted_row_count"),
            "gold_row_count": exe.get("gold_row_count"),
        })
    if rows:
        with (out / "per_question_results.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
