#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request

from sdmc.experiment import sqlite_path_for_db
from sdmc.jsonl import append_jsonl, read_jsonl
from sdmc.questions import load_questions
from sdmc.stage_b import build_repair_prompt, evaluate_readonly, explain_readonly, extract_sql


def post_chat(endpoint: str, model: str, prompt: str, max_tokens: int, temperature: float, timeout: int) -> dict[str, Any]:
    start = time.monotonic()
    last_error = ""
    current_max_tokens = max_tokens
    for attempt in range(3):
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": current_max_tokens,
            "stream": False,
        }
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{endpoint.rstrip('/')}/chat/completions",
            data=data,
            headers={"Content-Type": "application/json", "Authorization": "Bearer local-calibration"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
            return {
                "status": "success",
                "raw_response": content,
                "generated_sql": extract_sql(content),
                "latency": time.monotonic() - start,
                "usage": body.get("usage", {}),
                "attempts": attempt + 1,
                "requested_max_tokens": current_max_tokens,
            }
        except urlerror.HTTPError as e:
            last_error = f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:500]}"
            if e.code == 400 and "maximum context length" in last_error and current_max_tokens > 128:
                current_max_tokens = max(128, current_max_tokens // 2)
                continue
            if e.code not in {429, 500, 502, 503, 504}:
                break
        except Exception as e:
            last_error = str(e)
        if attempt < 2:
            time.sleep(2**attempt)
    return {
        "status": "api_error",
        "raw_response": "",
        "generated_sql": None,
        "latency": time.monotonic() - start,
        "usage": {},
        "error": last_error,
    }


def load_prompts(dry_output: Path, conditions: set[str]) -> dict[tuple[str, str], dict[str, Any]]:
    prompts = {}
    for row in read_jsonl(dry_output / "prompt_records.jsonl"):
        condition = str(row.get("condition_id"))
        if condition in conditions:
            prompts[(str(row["question_id"]), condition)] = row
    return prompts


def completed_pairs(output: Path) -> set[tuple[str, str]]:
    p = output / "executions.jsonl"
    if not p.exists():
        return set()
    return {(str(r.get("question_id")), str(r.get("condition_id"))) for r in read_jsonl(p)}


def prediction_pairs(output: Path) -> set[tuple[str, str]]:
    p = output / "predictions.jsonl"
    if not p.exists():
        return set()
    return {(str(r.get("question_id")), str(r.get("condition_id"))) for r in read_jsonl(p)}


def aggregate(output: Path) -> dict[str, Any]:
    execs = read_jsonl(output / "executions.jsonl")
    preds = read_jsonl(output / "predictions.jsonl")
    total = len(execs)
    matched = sum(1 for r in execs if r.get("execution_match") is True)
    success = sum(1 for r in execs if r.get("execution_status") == "success")
    valid_sql = sum(1 for r in preds if r.get("generated_sql"))
    runtime_errors = sum(1 for r in execs if r.get("execution_status") == "runtime_error")
    empty = sum(1 for r in preds if not r.get("generated_sql"))
    model_latency = [r.get("generation_latency_seconds") for r in preds if isinstance(r.get("generation_latency_seconds"), (int, float))]
    out = {
        "questions": total,
        "local_execution_match": matched / total if total else None,
        "execution_success_rate": success / total if total else None,
        "valid_sql_rate": valid_sql / total if total else None,
        "runtime_error_rate": runtime_errors / total if total else None,
        "empty_sql_rate": empty / total if total else None,
        "avg_generation_latency_seconds": sum(model_latency) / len(model_latency) if model_latency else None,
    }
    (output / "aggregate.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    with (output / "aggregate.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out.keys()))
        w.writeheader()
        w.writerow(out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True, choices=["spider", "bird"])
    ap.add_argument("--split", default="dev")
    ap.add_argument("--root", required=True)
    ap.add_argument("--store", required=True)
    ap.add_argument("--dry-output", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--endpoint", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--model-label", required=True)
    ap.add_argument("--conditions", default="SDMC")
    ap.add_argument("--sample", type=int, default=30)
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--max-output-tokens", type=int, default=512)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--enable-explain-repair", action="store_true")
    ap.add_argument("--enable-runtime-repair", action="store_true")
    ap.add_argument("--max-repair-attempts", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=1)
    args = ap.parse_args()

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    run_config = vars(args).copy()
    (output / "run_config.json").write_text(json.dumps(run_config, indent=2, ensure_ascii=False), encoding="utf-8")

    conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]
    prompts = load_prompts(Path(args.dry_output), set(conditions))
    questions = load_questions(args.dataset, args.split, args.root, sample=args.sample, seed=args.seed)
    done = completed_pairs(output)

    selected_ids = [str(q.question_id) for q in questions]
    (output / "question_ids.txt").write_text("\n".join(selected_ids) + "\n", encoding="utf-8")

    tasks = []
    pred_done = prediction_pairs(output)
    for q in questions:
        qid = str(q.question_id)
        sqlite_path = sqlite_path_for_db(args.store, q.database_id)
        if not sqlite_path:
            raise RuntimeError(f"missing sqlite path for database_id={q.database_id}")
        for condition in conditions:
            if (qid, condition) in done:
                continue
            prompt_row = prompts.get((qid, condition))
            if not prompt_row:
                raise RuntimeError(f"missing prompt for question_id={qid}, condition={condition}")
            tasks.append((q, condition, prompt_row, sqlite_path, (qid, condition) in pred_done))

    def run_task(item: tuple[Any, str, dict[str, Any], Path, bool]) -> tuple[dict[str, Any], dict[str, Any], bool]:
        q, condition, prompt_row, sqlite_path, has_stale_prediction = item
        gen = post_chat(args.endpoint, args.model, prompt_row["prompt"], args.max_output_tokens, args.temperature, args.timeout)
        repair_attempts = 0
        repair_sources: list[str] = []
        explain_check: dict[str, Any] | None = None
        pred = {
            "question_id": q.question_id,
            "condition_id": condition,
            "database_id": q.database_id,
            "dataset": args.dataset,
            "difficulty": q.difficulty,
            "model_label": args.model_label,
            "status": gen.get("status"),
            "generated_sql": gen.get("generated_sql"),
            "original_generated_sql": gen.get("generated_sql"),
            "repair_attempts": repair_attempts,
            "repair_sources": repair_sources,
            "explain_status": None,
            "explain_error": None,
            "generation_latency_seconds": gen.get("latency"),
            "usage": gen.get("usage", {}),
            "raw_response": gen.get("raw_response", ""),
            "error": gen.get("error"),
        }
        if gen.get("generated_sql") and q.gold_sql:
            if args.enable_explain_repair:
                explain_check = explain_readonly(sqlite_path, gen["generated_sql"])
                if not explain_check.get("ok") and repair_attempts < args.max_repair_attempts:
                    repair_prompt = build_repair_prompt(
                        prompt_row["prompt"],
                        gen["generated_sql"],
                        f"EXPLAIN QUERY PLAN error: {explain_check.get('error') or explain_check.get('explain_status')}",
                    )
                    repair = post_chat(args.endpoint, args.model, repair_prompt, args.max_output_tokens, args.temperature, args.timeout)
                    repair_attempts += 1
                    repair_sources.append("explain")
                    if repair.get("generated_sql"):
                        gen = repair
                        explain_check = explain_readonly(sqlite_path, gen["generated_sql"])
            exe = evaluate_readonly(sqlite_path, gen["generated_sql"], q.gold_sql)
            while (
                args.enable_runtime_repair
                and exe.get("execution_status") == "runtime_error"
                and repair_attempts < args.max_repair_attempts
            ):
                repair_prompt = build_repair_prompt(prompt_row["prompt"], gen["generated_sql"], str(exe.get("error") or ""))
                repair = post_chat(args.endpoint, args.model, repair_prompt, args.max_output_tokens, args.temperature, args.timeout)
                repair_attempts += 1
                repair_sources.append("runtime")
                if not repair.get("generated_sql"):
                    break
                gen = repair
                exe = evaluate_readonly(sqlite_path, gen["generated_sql"], q.gold_sql)
            pred.update({
                "status": gen.get("status"),
                "generated_sql": gen.get("generated_sql"),
                "repair_attempts": repair_attempts,
                "repair_sources": repair_sources,
                "explain_status": (explain_check or {}).get("explain_status"),
                "explain_error": (explain_check or {}).get("error"),
                "raw_response": gen.get("raw_response", pred.get("raw_response", "")),
                "generation_latency_seconds": gen.get("latency"),
                "usage": gen.get("usage", {}),
                "error": gen.get("error"),
            })
            exe.update({
                "question_id": q.question_id,
                "condition_id": condition,
                "database_id": q.database_id,
                "dataset": args.dataset,
                "difficulty": q.difficulty,
                "model_label": args.model_label,
            })
        else:
            exe = {
                "question_id": q.question_id,
                "condition_id": condition,
                "database_id": q.database_id,
                "dataset": args.dataset,
                "difficulty": q.difficulty,
                "model_label": args.model_label,
                "execution_status": "not_evaluated",
                "execution_match": False,
            }
        return pred, exe, has_stale_prediction

    if args.concurrency <= 1:
        iterator = map(run_task, tasks)
        for pred, exe, has_stale_prediction in iterator:
            if not has_stale_prediction:
                append_jsonl(output / "predictions.jsonl", pred)
            append_jsonl(output / "executions.jsonl", exe)
    else:
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = [executor.submit(run_task, task) for task in tasks]
            completed = 0
            total_tasks = len(futures)
            for future in as_completed(futures):
                pred, exe, has_stale_prediction = future.result()
                if not has_stale_prediction:
                    append_jsonl(output / "predictions.jsonl", pred)
                append_jsonl(output / "executions.jsonl", exe)
                completed += 1
                if completed % 50 == 0 or completed == total_tasks:
                    print(json.dumps({"completed_new_pairs": completed, "total_new_pairs": total_tasks}, ensure_ascii=False), flush=True)

    result = aggregate(output)
    print(json.dumps({"status": "ok", "output": str(output), **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
