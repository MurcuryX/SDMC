#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


SPIDER_EVAL_DIR = Path(
    "<WORKSPACE_ROOT>/TiInsight/official_tiinsight/chat2query_benchmark/spider/test-suite-sql-eval"
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_spider_hardness(root: Path, split: str = "dev") -> dict[str, str]:
    sys.path.insert(0, str(SPIDER_EVAL_DIR))
    from evaluation import Evaluator  # type: ignore
    from process_sql import Schema, get_schema, get_sql  # type: ignore

    evaluator = Evaluator()
    rows = json.loads((root / f"{split}.json").read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    schema_cache: dict[str, Schema] = {}
    for idx, row in enumerate(rows):
        qid = f"spider-{split}-{idx}"
        db_id = row["db_id"]
        if db_id not in schema_cache:
            db_path = root / "database" / db_id / f"{db_id}.sqlite"
            schema_cache[db_id] = Schema(get_schema(str(db_path)))
        try:
            gold_sql = get_sql(schema_cache[db_id], row["query"])
            out[qid] = evaluator.eval_hardness(gold_sql)
        except Exception:
            out[qid] = "unknown"
    return out


def load_bird_difficulty(root: Path, split: str = "dev") -> dict[str, str]:
    path = root / f"{split}.json"
    if not path.exists():
        path = root / split / f"{split}.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {str(row.get("question_id", f"bird-{split}-{idx}")): str(row.get("difficulty") or "unknown") for idx, row in enumerate(rows)}


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def pct(num: int, den: int) -> float | None:
    return round(num / den * 100, 2) if den else None


def usage_value(usage: dict[str, Any], key: str) -> int:
    value = usage.get(key)
    return int(value) if isinstance(value, int) else 0


def aggregate_run(run_dir: Path, dataset: str, root: Path, split: str) -> list[dict[str, Any]]:
    preds = read_jsonl(run_dir / "predictions.jsonl")
    execs = read_jsonl(run_dir / "executions.jsonl")
    pred_map = {(str(r.get("question_id")), str(r.get("condition_id"))): r for r in preds}
    if dataset == "spider":
        difficulty = load_spider_hardness(root, split)
        levels = ["easy", "medium", "hard", "extra", "all"]
    else:
        difficulty = load_bird_difficulty(root, split)
        levels = sorted(set(difficulty.values()) | {"all"})

    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for exe in execs:
        condition = str(exe.get("condition_id"))
        qid = str(exe.get("question_id"))
        level = difficulty.get(qid, "unknown")
        row = {"exe": exe, "pred": pred_map.get((qid, condition), {})}
        buckets[(condition, level)].append(row)
        buckets[(condition, "all")].append(row)

    out: list[dict[str, Any]] = []
    for condition in sorted({k[0] for k in buckets}):
        for level in levels:
            rows = buckets.get((condition, level), [])
            if not rows:
                continue
            n = len(rows)
            matched = sum(1 for r in rows if r["exe"].get("execution_match") is True)
            success = sum(1 for r in rows if r["exe"].get("execution_status") == "success")
            runtime = sum(1 for r in rows if r["exe"].get("execution_status") == "runtime_error")
            valid = sum(1 for r in rows if r["pred"].get("generated_sql"))
            gen_lat = [float(r["pred"]["generation_latency_seconds"]) for r in rows if isinstance(r["pred"].get("generation_latency_seconds"), (int, float))]
            exe_lat = [float(r["exe"]["latency"]) for r in rows if isinstance(r["exe"].get("latency"), (int, float))]
            prompt_tokens = [usage_value(r["pred"].get("usage") or {}, "prompt_tokens") for r in rows]
            completion_tokens = [usage_value(r["pred"].get("usage") or {}, "completion_tokens") for r in rows]
            repair_attempts = [int(r["pred"].get("repair_attempts") or 0) for r in rows]
            out.append({
                "run": run_dir.name,
                "dataset": dataset,
                "condition": condition,
                "difficulty": level,
                "N": n,
                "EX_pct": pct(matched, n),
                "valid_sql_pct": pct(valid, n),
                "execution_success_pct": pct(success, n),
                "runtime_error_pct": pct(runtime, n),
                "avg_generation_latency_s": round(mean(gen_lat) or 0, 4),
                "avg_execution_latency_s": round(mean(exe_lat) or 0, 4),
                "avg_prompt_tokens": round(mean([x for x in prompt_tokens if x]) or 0, 2),
                "avg_completion_tokens": round(mean([x for x in completion_tokens if x]) or 0, 2),
                "total_prompt_tokens": sum(prompt_tokens),
                "total_completion_tokens": sum(completion_tokens),
                "total_repair_attempts": sum(repair_attempts),
            })
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--dataset", required=True, choices=["spider", "bird"])
    parser.add_argument("--root", required=True)
    parser.add_argument("--split", default="dev")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    rows = aggregate_run(Path(args.run_dir), args.dataset, Path(args.root), args.split)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "run", "dataset", "condition", "difficulty", "N", "EX_pct", "valid_sql_pct",
        "execution_success_pct", "runtime_error_pct", "avg_generation_latency_s",
        "avg_execution_latency_s", "avg_prompt_tokens", "avg_completion_tokens",
        "total_prompt_tokens", "total_completion_tokens", "total_repair_attempts",
    ]
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fields)
        writer.writeheader()
        writer.writerows(rows)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
