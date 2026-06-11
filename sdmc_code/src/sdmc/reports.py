from __future__ import annotations

from pathlib import Path
from typing import Any
import csv
import json
import sqlite3

from sdmc.jsonl import read_jsonl


def stage_a_sanity(output_root: str | Path) -> list[dict[str, Any]]:
    root = Path(output_root)
    rows = []
    for store in sorted(root.glob("context_build*/*/*/context_store.sqlite")):
        conn = sqlite3.connect(store)
        conn.row_factory = sqlite3.Row
        db = conn.execute("SELECT dataset_name, split_name, COUNT(*) n FROM databases").fetchone()
        complete = conn.execute("SELECT COUNT(*) n FROM databases WHERE build_status IN (?, ?)", ("context_complete", "graph_complete")).fetchone()["n"]
        graph_summaries = conn.execute("SELECT COUNT(*) n FROM dataset_graph_summary WHERE build_status=?", ("graph_complete",)).fetchone()["n"]
        failed = conn.execute("SELECT COUNT(*) n FROM provenance_records WHERE execution_status=?", ("failed",)).fetchone()["n"]
        failed_context = conn.execute("SELECT COUNT(*) n FROM context_items WHERE execution_status=?", ("failed",)).fetchone()["n"]
        nodes = conn.execute("SELECT COUNT(*) n FROM graph_nodes").fetchone()["n"]
        edges = conn.execute("SELECT COUNT(*) n FROM graph_edges").fetchone()["n"]
        rows.append({
            "dataset": db["dataset_name"],
            "split": db["split_name"],
            "build_root": store.parent.parent.parent.name,
            "databases": db["n"],
            "context_store_complete": complete,
            "context_graph_complete": graph_summaries,
            "failed_provenance": failed,
            "failed_context_items": failed_context,
            "graph_nodes": nodes,
            "graph_edges": edges,
            "store_size_bytes": store.stat().st_size,
        })
        conn.close()
    return rows


def aggregate_experiment(output_dir: str | Path) -> dict[str, Any]:
    out = Path(output_dir)
    prompts = read_jsonl(out / "prompt_records.jsonl")
    selections = read_jsonl(out / "selection_records.jsonl")
    preds = read_jsonl(out / "predictions.jsonl")
    execs = read_jsonl(out / "executions.jsonl")
    conds = sorted({r.get("condition_id") for r in prompts})
    rows = []
    for cond in conds:
        p = [r for r in prompts if r.get("condition_id") == cond]
        sel = [r for r in selections if r.get("condition_id") == cond]
        pr = [r for r in preds if r.get("condition_id") == cond]
        ex = [r for r in execs if r.get("condition_id") == cond]
        n = len(p)
        matches = [r.get("execution_match") for r in ex if r.get("execution_match") is not None]
        valid = [r for r in pr if r.get("generated_sql")]
        usage = [r.get("usage") or {} for r in pr]
        in_tok = [u.get("prompt_tokens") for u in usage if u.get("prompt_tokens") is not None]
        out_tok = [u.get("completion_tokens") for u in usage if u.get("completion_tokens") is not None]
        gen_lat = [r.get("generation_latency_seconds") for r in pr if r.get("generation_latency_seconds") is not None]
        exec_lat = [r.get("latency") for r in ex if r.get("latency") is not None]
        render_lat = [r.get("render_seconds") for r in sel if r.get("render_seconds") is not None]
        repair_attempts = [r.get("repair_attempts", 0) or 0 for r in pr]
        explain_errors = [r for r in pr if r.get("explain_status") in {"error", "invalid_sql"}]
        explain_repairs = [r for r in pr if "explain" in (r.get("repair_sources") or [])]
        runtime_repairs = [r for r in pr if "runtime" in (r.get("repair_sources") or [])]
        rows.append({
            "condition_id": cond,
            "N": n,
            "execution_match_pct": round(100 * sum(1 for m in matches if m) / len(matches), 2) if matches else None,
            "valid_sql_pct": round(100 * len(valid) / len(pr), 2) if pr else None,
            "runtime_error_pct": round(100 * sum(1 for r in ex if r.get("execution_status") == "runtime_error") / len(ex), 2) if ex else None,
            "avg_estimated_input_tokens": round(sum(r.get("estimated_input_tokens", 0) for r in p) / n, 2) if n else None,
            "prompt_truncation_count": sum(1 for r in p if (r.get("prompt_budget_trace") or {}).get("applied")),
            "condition_warning_count": sum(1 for r in p if r.get("condition_warnings")),
            "avg_selected_nodes": round(sum(r.get("selected_node_count", 0) for r in sel) / len(sel), 2) if sel else None,
            "avg_selected_edges": round(sum(r.get("selected_edge_count", 0) for r in sel) / len(sel), 2) if sel else None,
            "candidate_truncation_count": sum(1 for r in sel if r.get("candidate_truncation_flag")),
            "avg_real_input_tokens": round(sum(in_tok) / len(in_tok), 2) if in_tok else None,
            "avg_real_output_tokens": round(sum(out_tok) / len(out_tok), 2) if out_tok else None,
            "avg_generation_latency_seconds": round(sum(gen_lat) / len(gen_lat), 3) if gen_lat else None,
            "avg_execution_latency_seconds": round(sum(exec_lat) / len(exec_lat), 3) if exec_lat else None,
            "avg_render_seconds": round(sum(render_lat) / len(render_lat), 3) if render_lat else None,
            "total_repair_attempts": sum(repair_attempts),
            "explain_error_count": len(explain_errors),
            "explain_repair_count": len(explain_repairs),
            "runtime_repair_count": len(runtime_repairs),
            "leakage_flags": sum(1 for r in p if r.get("leakage_flags")),
        })
    if rows:
        with (out / "aggregate_results.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    complexity_rows = []
    for cond in conds:
        for complexity in sorted({(r.get("query_features") or {}).get("estimated_complexity", "unknown") for r in prompts if r.get("condition_id") == cond}):
            p = [r for r in prompts if r.get("condition_id") == cond and (r.get("query_features") or {}).get("estimated_complexity", "unknown") == complexity]
            ex = [
                r for r in execs
                if r.get("condition_id") == cond and any(str(pr.get("question_id")) == str(r.get("question_id")) for pr in p)
            ]
            matches = [r.get("execution_match") for r in ex if r.get("execution_match") is not None]
            complexity_rows.append({
                "condition_id": cond,
                "estimated_complexity": complexity,
                "N": len(p),
                "execution_match_pct": round(100 * sum(1 for m in matches if m) / len(matches), 2) if matches else None,
                "avg_estimated_input_tokens": round(sum(r.get("estimated_input_tokens", 0) for r in p) / len(p), 2) if p else None,
            })
    if complexity_rows:
        with (out / "aggregate_by_complexity.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(complexity_rows[0].keys()))
            writer.writeheader()
            writer.writerows(complexity_rows)
    return {"conditions": rows}


def paired_analysis(output_dir: str | Path, baseline: str = "RAW_SCHEMA", ours: str = "SDMC") -> dict[str, int]:
    execs = read_jsonl(Path(output_dir) / "executions.jsonl")
    by_q: dict[str, dict[str, Any]] = {}
    for r in execs:
        by_q.setdefault(str(r.get("question_id")), {})[r.get("condition_id")] = r.get("execution_match")
    counts = {"all_correct": 0, "all_wrong": 0, "sdmc_unique_gain": 0, "sdmc_miss": 0, "mixed_other": 0}
    for _, m in by_q.items():
        b = m.get(baseline)
        o = m.get(ours)
        if b is True and o is True:
            counts["all_correct"] += 1
        elif b is False and o is False:
            counts["all_wrong"] += 1
        elif b is False and o is True:
            counts["sdmc_unique_gain"] += 1
        elif b is True and o is False:
            counts["sdmc_miss"] += 1
        else:
            counts["mixed_other"] += 1
    out = Path(output_dir)
    (out / "paired_summary.json").write_text(json.dumps(counts, indent=2), encoding="utf-8")
    return counts
