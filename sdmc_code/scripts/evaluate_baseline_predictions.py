#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from sdmc.experiment import sqlite_path_for_db
from sdmc.jsonl import write_jsonl
from sdmc.questions import QuestionExample, load_questions
from sdmc.stage_b import evaluate_readonly, extract_sql


def load_prediction_rows(path: Path, fmt: str) -> list[dict[str, Any]]:
    if fmt == "auto":
        suffix = path.suffix.lower()
        if suffix == ".jsonl":
            fmt = "jsonl"
        elif suffix == ".json":
            fmt = "json_map"
        else:
            fmt = "lines"
    if fmt == "lines":
        return [{"index": i, "sql": line.strip()} for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()) if line.strip()]
    if fmt == "jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if fmt == "json_map":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return [{"question_id": str(k), "sql": v} for k, v in data.items()]
        if isinstance(data, list):
            rows: list[dict[str, Any]] = []
            for i, item in enumerate(data):
                if isinstance(item, dict):
                    rows.append(item)
                elif isinstance(item, list) and len(item) >= 2:
                    # Some BIRD baselines, including MAC-SQL, emit
                    # [question, "SQL\t----- bird -----\tdb_id"].
                    rows.append({"index": i, "question": item[0], "sql": item[1]})
                else:
                    rows.append({"index": i, "sql": item})
            return rows
    raise ValueError(f"unsupported prediction format: {fmt}")


def normalize_bird_sql(value: Any) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    text = str(value).strip()
    if "\t----- bird -----\t" in text:
        sql, db_id = text.split("\t----- bird -----\t", 1)
        return sql.strip(), db_id.strip()
    return text, None


def build_prediction_map(rows: list[dict[str, Any]], questions: list[QuestionExample]) -> dict[str, dict[str, Any]]:
    by_qid: dict[str, dict[str, Any]] = {}
    by_index: dict[int, dict[str, Any]] = {}
    for row in rows:
        if "question_id" in row:
            by_qid[str(row["question_id"])] = row
        elif "index" in row:
            by_index[int(row["index"])] = row
    out: dict[str, dict[str, Any]] = {}
    for idx, q in enumerate(questions):
        qid = str(q.question_id)
        aliases = [qid]
        if qid.startswith("spider-"):
            aliases.append(qid.rsplit("-", 1)[-1])
        row = None
        for alias in aliases:
            row = by_qid.get(alias)
            if row is not None:
                break
        if row is None:
            row = by_index.get(idx)
        if row is not None:
            out[qid] = row
    return out


def row_sql(row: dict[str, Any]) -> tuple[str | None, str]:
    for key in ("generated_sql", "sql", "prediction", "predicted_sql", "query"):
        if key in row:
            sql, _ = normalize_bird_sql(row.get(key))
            return (extract_sql(sql) if sql else None), str(row.get(key) or "")
    return None, ""


def aggregate(output: Path) -> dict[str, Any]:
    execs = [json.loads(line) for line in (output / "executions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    preds = [json.loads(line) for line in (output / "predictions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    total = len(execs)
    matched = sum(1 for row in execs if row.get("execution_match") is True)
    success = sum(1 for row in execs if row.get("execution_status") == "success")
    valid_sql = sum(1 for row in preds if row.get("generated_sql"))
    runtime_errors = sum(1 for row in execs if row.get("execution_status") == "runtime_error")
    not_eval = sum(1 for row in execs if row.get("execution_status") == "not_evaluated")
    out = {
        "questions": total,
        "local_execution_match": matched / total if total else None,
        "execution_success_rate": success / total if total else None,
        "valid_sql_rate": valid_sql / total if total else None,
        "runtime_error_rate": runtime_errors / total if total else None,
        "not_evaluated_rate": not_eval / total if total else None,
    }
    (output / "aggregate.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    with (output / "aggregate.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(out))
        writer.writeheader()
        writer.writerow(out)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert baseline predictions to SDMC JSONL and evaluate with local execution match.")
    parser.add_argument("--dataset", required=True, choices=["spider", "bird"])
    parser.add_argument("--split", default="dev")
    parser.add_argument("--root", required=True)
    parser.add_argument("--store", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--input-format", choices=["auto", "lines", "jsonl", "json_map"], default="auto")
    parser.add_argument("--output", required=True)
    parser.add_argument("--condition-id", required=True)
    parser.add_argument("--model-label", default="gemma4_26b")
    parser.add_argument("--limit", type=int, help="Evaluate only the first N questions; intended for smoke runs.")
    parser.add_argument("--sample", type=int)
    parser.add_argument("--seed", type=int, default=13)
    args = parser.parse_args()

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    run_config = vars(args).copy()
    (output / "run_config.json").write_text(json.dumps(run_config, indent=2, ensure_ascii=False), encoding="utf-8")

    questions = load_questions(args.dataset, args.split, args.root, limit=args.limit, sample=args.sample, seed=args.seed)
    rows = load_prediction_rows(Path(args.input), args.input_format)
    pred_map = build_prediction_map(rows, questions)

    predictions = []
    executions = []
    for q in questions:
        source = pred_map.get(str(q.question_id))
        sql = None
        raw = ""
        if source is not None:
            sql, raw = row_sql(source)
        pred = {
            "question_id": q.question_id,
            "condition_id": args.condition_id,
            "database_id": q.database_id,
            "dataset": args.dataset,
            "difficulty": q.difficulty,
            "model_label": args.model_label,
            "status": "success" if sql else "missing_prediction",
            "generated_sql": sql,
            "raw_response": raw,
        }
        sqlite_path = sqlite_path_for_db(args.store, q.database_id)
        if sql and q.gold_sql and sqlite_path:
            exe = evaluate_readonly(sqlite_path, sql, q.gold_sql)
        else:
            exe = {"execution_status": "not_evaluated", "execution_match": False}
        exe.update({
            "question_id": q.question_id,
            "condition_id": args.condition_id,
            "database_id": q.database_id,
            "dataset": args.dataset,
            "difficulty": q.difficulty,
            "model_label": args.model_label,
        })
        predictions.append(pred)
        executions.append(exe)

    write_jsonl(output / "predictions.jsonl", predictions)
    write_jsonl(output / "executions.jsonl", executions)
    result = aggregate(output)
    print(json.dumps({"status": "ok", "output": str(output), **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
