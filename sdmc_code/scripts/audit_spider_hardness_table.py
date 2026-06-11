#!/usr/bin/env python3
"""Build Spider hardness breakdowns for final SDMC RQ tables."""

from __future__ import annotations

import csv
import json
import re
import sys
import types
from collections import Counter, defaultdict
from pathlib import Path


def _simple_word_tokenize(text: str) -> list[str]:
    return re.findall(
        r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*|\d+\.\d+|\d+|>=|<=|!=|<>|[(),*=<>;+\-/]|'[^']*'|\"[^\"]*\"",
        text,
    )


def _install_nltk_shim() -> None:
    nltk = types.ModuleType("nltk")
    nltk.word_tokenize = _simple_word_tokenize
    sys.modules["nltk"] = nltk
    tqdm_module = types.ModuleType("tqdm")
    tqdm_module.tqdm = lambda iterable=None, *args, **kwargs: iterable if iterable is not None else []
    sys.modules.setdefault("tqdm", tqdm_module)


def _fallback_hardness(sql: str) -> str:
    q = f" {sql.lower()} "
    comp1 = sum(
        token in q
        for token in [" where ", " group by ", " order by ", " limit ", " join ", " or ", " like "]
    )
    comp2 = sum(token in q for token in [" except ", " union ", " intersect "])
    others = sum(q.count(token) for token in [" count(", " avg(", " sum(", " max(", " min("])
    others += max(0, q.split(" from ")[0].count(","))
    if comp1 <= 1 and others == 0 and comp2 == 0:
        return "easy"
    if (others <= 2 and comp1 <= 1 and comp2 == 0) or (comp1 <= 2 and others < 2 and comp2 == 0):
        return "medium"
    if (
        (others > 2 and comp1 <= 2 and comp2 == 0)
        or (2 < comp1 <= 3 and others <= 2 and comp2 == 0)
        or (comp1 <= 1 and others == 0 and comp2 <= 1)
    ):
        return "hard"
    return "extra"


WHERE_OPS = ("not", "between", "=", ">", "<", ">=", "<=", "!=", "in", "like", "is", "exists")
AGG_OPS = ("none", "max", "min", "count", "sum", "avg")


def _has_agg(unit: object) -> bool:
    return isinstance(unit, (list, tuple)) and len(unit) > 0 and unit[0] != AGG_OPS.index("none")


def _count_agg(units: list[object]) -> int:
    return len([unit for unit in units if _has_agg(unit)])


def _nested_sql(sql: dict[str, object]) -> list[dict[str, object]]:
    nested = []
    for cond_unit in sql["from"]["conds"][::2] + sql["where"][::2] + sql["having"][::2]:
        if cond_unit[3] is not None and isinstance(cond_unit[3], dict):
            nested.append(cond_unit[3])
        if cond_unit[4] is not None and isinstance(cond_unit[4], dict):
            nested.append(cond_unit[4])
    for op in ["intersect", "except", "union"]:
        if sql[op] is not None:
            nested.append(sql[op])
    return nested


def _spider_hardness(parsed_sql: dict[str, object]) -> str:
    comp1 = 0
    if len(parsed_sql["where"]) > 0:
        comp1 += 1
    if len(parsed_sql["groupBy"]) > 0:
        comp1 += 1
    if len(parsed_sql["orderBy"]) > 0:
        comp1 += 1
    if parsed_sql["limit"] is not None:
        comp1 += 1
    if len(parsed_sql["from"]["table_units"]) > 0:
        comp1 += len(parsed_sql["from"]["table_units"]) - 1
    ao = parsed_sql["from"]["conds"][1::2] + parsed_sql["where"][1::2] + parsed_sql["having"][1::2]
    comp1 += len([token for token in ao if token == "or"])
    cond_units = parsed_sql["from"]["conds"][::2] + parsed_sql["where"][::2] + parsed_sql["having"][::2]
    comp1 += len([cond_unit for cond_unit in cond_units if cond_unit[1] == WHERE_OPS.index("like")])

    comp2 = len(_nested_sql(parsed_sql))

    others = 0
    agg_count = _count_agg(parsed_sql["select"][1])
    agg_count += _count_agg(parsed_sql["where"][::2])
    agg_count += _count_agg(parsed_sql["groupBy"])
    if len(parsed_sql["orderBy"]) > 0:
        agg_count += _count_agg(
            [unit[1] for unit in parsed_sql["orderBy"][1] if unit[1]]
            + [unit[2] for unit in parsed_sql["orderBy"][1] if unit[2]]
        )
    agg_count += _count_agg(parsed_sql["having"])
    if agg_count > 1:
        others += 1
    if len(parsed_sql["select"][1]) > 1:
        others += 1
    if len(parsed_sql["where"]) > 1:
        others += 1
    if len(parsed_sql["groupBy"]) > 1:
        others += 1

    if comp1 <= 1 and others == 0 and comp2 == 0:
        return "easy"
    if (others <= 2 and comp1 <= 1 and comp2 == 0) or (comp1 <= 2 and others < 2 and comp2 == 0):
        return "medium"
    if (
        (others > 2 and comp1 <= 2 and comp2 == 0)
        or (2 < comp1 <= 3 and others <= 2 and comp2 == 0)
        or (comp1 <= 1 and others == 0 and comp2 <= 1)
    ):
        return "hard"
    return "extra"


def _question_index(question_id: object) -> int | None:
    match = re.search(r"(\d+)$", str(question_id))
    return int(match.group(1)) if match else None


def _percent(correct: int, total: int) -> str:
    return f"{100 * correct / total:.2f}" if total else "NA"


def _aggregate_executions(path: Path, hardness: list[str], condition_id: str | None = None) -> dict[str, list[int]]:
    values: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    with path.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if condition_id is not None and row.get("condition_id") != condition_id:
                continue
            index = _question_index(row.get("question_id"))
            if index is None or index >= len(hardness):
                continue
            bucket = hardness[index]
            values[bucket][1] += 1
            values[bucket][0] += 1 if row.get("execution_match") else 0
    return values


def _aggregate_tisql_eval(path: Path, hardness: list[str]) -> dict[str, list[int]]:
    values: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    data = json.loads(path.read_text())
    for row in data.get("rows", []):
        index = _question_index(row.get("question_id"))
        if index is None or index >= len(hardness):
            continue
        bucket = hardness[index]
        values[bucket][1] += 1
        values[bucket][0] += 1 if row.get("correct") else 0
    return values


def _table_row(method: str, values: dict[str, list[int]]) -> dict[str, object]:
    row: dict[str, object] = {"method": method}
    for bucket in ["easy", "medium", "hard", "extra"]:
        correct, total = values.get(bucket, [0, 0])
        row[bucket] = _percent(correct, total)
        row[f"{bucket}_n"] = total
    correct_all = sum(value[0] for value in values.values())
    total_all = sum(value[1] for value in values.values())
    row["all"] = _percent(correct_all, total_all)
    row["all_n"] = total_all
    return row


def main() -> None:
    root = Path.cwd()
    base = root / "outputs/rq_final_20260608_023504"
    output_dir = base / "results_collected/final_tables_audit"
    output_dir.mkdir(parents=True, exist_ok=True)

    spider_root = Path("<SERVER1_DATA_ROOT>/SDMC_remote_run/local_data/roots/spider")
    dev = json.loads((spider_root / "dev.json").read_text())

    _install_nltk_shim()
    sys.path.insert(0, str(root / "external_baselines/MAC-SQL/evaluation"))
    from process_sql import Schema, get_schema, get_sql  # type: ignore

    hardness: list[str] = []
    failed: list[dict[str, object]] = []
    for index, row in enumerate(dev):
        db_id = row["db_id"]
        db_path = spider_root / "database" / db_id / f"{db_id}.sqlite"
        try:
            schema = Schema(get_schema(str(db_path)))
            parsed = get_sql(schema, row["query"])
            bucket = _spider_hardness(parsed)
        except Exception as exc:  # Keep the table fillable; record every fallback.
            bucket = _fallback_hardness(row["query"])
            failed.append({"index": index, "error": str(exc), "fallback": bucket})
        hardness.append(bucket)

    rows: list[dict[str, object]] = []
    rq2_exec = base / "rq2_spider_gemma4_p18114_full_20260609_010124/executions.jsonl"
    if rq2_exec.exists():
        rows.append(_table_row("RAW_SCHEMA", _aggregate_executions(rq2_exec, hardness, "RAW_SCHEMA")))
        rows.append(_table_row("SDMC", _aggregate_executions(rq2_exec, hardness, "SDMC")))

    execution_rows = [
        ("MAC-SQL", base / "baseline_runs/macsql_spider_full_gemma4_p18114_20260608/eval/executions.jsonl"),
        ("DAIL-SQL", base / "baseline_runs/dailsql_spider_full_gemma4_p18114_20260608/eval/executions.jsonl"),
        ("DIN-SQL", base / "baseline_runs/dinsql_spider_full_gemma4_p18114_retry_20260608/eval/executions.jsonl"),
        ("CHESS", base / "baseline_runs/chess_spider_full_gemma4_p18114_20260609/eval/executions.jsonl"),
        (
            "RSL-SQL_PRELIM_ONLY",
            base / "baseline_runs/rsl_sql_spider_preliminary_only_gemma4_20260610/eval/executions.jsonl",
        ),
        ("RSL-SQL", base / "baseline_runs/rsl_sql_spider_full_gemma4_p18114_20260610/eval/executions.jsonl"),
    ]
    for method, path in execution_rows:
        if path.exists():
            rows.append(_table_row(method, _aggregate_executions(path, hardness)))

    tisql_eval = base / "rq3_runs/tisql/table1_tisql_spider_llm_gemma4_p18115_20260610/eval.json"
    if tisql_eval.exists():
        rows.append(_table_row("TiSQL", _aggregate_tisql_eval(tisql_eval, hardness)))

    fields = [
        "method",
        "easy",
        "easy_n",
        "medium",
        "medium_n",
        "hard",
        "hard_n",
        "extra",
        "extra_n",
        "all",
        "all_n",
    ]
    with (output_dir / "rq1_table1_spider_by_hardness.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fields)
        writer.writeheader()
        writer.writerows(rows)

    audit = {
        "hardness_counts": Counter(hardness),
        "parse_failed_count": len(failed),
        "parse_failed_examples": failed[:20],
        "note": "Hardness uses Spider evaluator logic with a lightweight tokenizer shim; failed parses use a recorded SQL-feature fallback.",
    }
    (output_dir / "spider_hardness_audit.json").write_text(json.dumps(audit, indent=2))
    print((output_dir / "rq1_table1_spider_by_hardness.csv").read_text())
    print(json.dumps(audit, indent=2))


if __name__ == "__main__":
    main()
