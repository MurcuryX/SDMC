#!/usr/bin/env python3
"""Build the SDMC final RQ results Word document without external packages."""

from __future__ import annotations

import csv
import json
import math
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path
from xml.sax.saxutils import escape


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
OUTPUT = PROJECT / "Docs" / "SDMC_RQ_Experiment_Results.docx"


REMOTE_SUMMARY = json.loads(
    r'''{"table1_bird": {"RAW_SCHEMA": 28.23, "SDMC": 38.53, "MAC-SQL": 21.19, "DAIL-SQL": 53.65, "DIN-SQL": 16.56, "CHESS": 46.54, "RSL-SQL": 62.78, "TiSQL": 58.34}, "rq2": {"RAW_SCHEMA": {"easy": 86.69, "medium": 74.44, "hard": 68.39, "extra": 50.6, "all": 72.53, "n": 1034, "bird": 28.23}, "SDMC (Store + Graph)": {"easy": 93.95, "medium": 79.82, "hard": 81.03, "extra": 64.46, "all": 80.95, "n": 1034, "bird": 38.53}, "SDMC_FLAT_STORE": {"easy": 90.73, "medium": 77.35, "hard": 75.86, "extra": 60.24, "all": 77.56, "n": 1034, "bird": 30.05}, "SDMC_FULL": {"easy": 93.95, "medium": 78.7, "hard": 76.44, "extra": 60.24, "all": 79.01, "n": 1034, "bird": 30.31}, "SDMC_GRAPH_NO_REL": {"easy": 94.35, "medium": 78.92, "hard": 79.31, "extra": 60.24, "all": 79.69, "n": 1034, "bird": 36.44}, "SDMC_GRAPH_SCHEMA_ONLY": {"easy": 85.48, "medium": 76.68, "hard": 71.84, "extra": 57.83, "all": 74.95, "n": 1034, "bird": 29.14}}, "rq3": {"MAC-SQL|Schema-based": {"easy": 95.16, "medium": 79.82, "hard": 83.91, "extra": 65.66, "all": 81.91, "n": 1034, "bird": 60.95}, "MAC-SQL|LLM-based": {"easy": 95.56, "medium": 81.39, "hard": 83.33, "extra": 64.46, "all": 82.4, "n": 1034, "bird": 49.22}, "MAC-SQL|SQL-based": {"easy": 95.56, "medium": 80.72, "hard": 84.48, "extra": 67.47, "all": 82.79, "n": 1034, "bird": 49.35}, "TiSQL|Schema-based": {"easy": 90.32, "medium": 85.2, "hard": 71.26, "extra": 59.64, "all": 79.98, "n": 1034, "bird": 61.21}, "TiSQL|LLM-based": {"easy": 86.69, "medium": 83.41, "hard": 70.69, "extra": 58.43, "all": 78.05, "n": 1034, "bird": 58.54}, "TiSQL|SQL-based": {"easy": 90.73, "medium": 83.86, "hard": 73.56, "extra": 60.24, "all": 79.98, "n": 1034, "bird": 59.13}, "SDMC|Schema-based": {"easy": 86.69, "medium": 74.22, "hard": 68.39, "extra": 50.6, "all": 72.44, "n": 1034, "bird": 28.23}, "SDMC|LLM-based": {"easy": 93.55, "medium": 81.17, "hard": 80.46, "extra": 64.46, "all": 81.33, "n": 1034, "bird": 39.7}, "SDMC|SQL-based": {"easy": 94.35, "medium": 80.04, "hard": 79.89, "extra": 65.06, "all": 81.04, "n": 1034, "bird": 38.14}}, "rq4": {"SDMC full": {"easy": 94.35, "medium": 80.04, "hard": 81.03, "extra": 65.06, "all": 81.24, "n": 1034, "bird": 38.4}, "w/o Column Context": {"easy": 89.11, "medium": 76.01, "hard": 74.14, "extra": 60.24, "all": 76.31, "n": 1034, "bird": 31.03}, "w/o Table Context": {"easy": 93.95, "medium": 80.27, "hard": 80.46, "extra": 64.46, "all": 81.04, "n": 1034, "bird": 37.74}, "w/o Database Context": {"easy": 93.95, "medium": 80.04, "hard": 80.46, "extra": 64.46, "all": 80.95, "n": 1034, "bird": 38.33}, "only Column Context": {"easy": 93.95, "medium": 80.27, "hard": 79.89, "extra": 63.25, "all": 80.75, "n": 1034, "bird": 38.46}, "only Table Context": {"easy": 89.52, "medium": 76.23, "hard": 74.14, "extra": 60.84, "all": 76.6, "n": 1034, "bird": 30.77}, "only Database Context": {"easy": 88.31, "medium": 76.46, "hard": 72.41, "extra": 59.04, "all": 75.82, "n": 1034, "bird": 31.16}}}'''
)


def fmt(value: object) -> str:
    if value is None:
        return "---"
    if isinstance(value, str):
        return value
    return f"{float(value):.2f}"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def load_csv_row(path: Path, condition_id: str | None = None) -> dict:
    rows = list(csv.DictReader(path.open()))
    if condition_id is None:
        return rows[0]
    for row in rows:
        if row.get("condition_id") == condition_id:
            return row
    raise KeyError(condition_id)


def build_hardness() -> list[str]:
    cached = PROJECT / "outputs/rq_final_20260608_023504/results_collected/final_tables_audit/spider_hardness_by_index.json"
    if cached.exists():
        values = json.loads(cached.read_text())
        if len(values) == 1034:
            return values

    sys.path.insert(0, str(PROJECT / "scripts"))
    import audit_spider_hardness_table as ah

    ah._install_nltk_shim()
    sys.path.insert(0, str(PROJECT / "external_baselines/MAC-SQL/evaluation"))
    from process_sql import Schema, get_schema, get_sql  # type: ignore

    spider_root = PROJECT / "outputs/pilot_local/roots/spider"
    dev = load_json(spider_root / "dev.json")
    buckets = []
    for row in dev:
        db_id = row["db_id"]
        db_path = spider_root / "database" / db_id / f"{db_id}.sqlite"
        schema = Schema(get_schema(str(db_path)))
        parsed = get_sql(schema, row["query"])
        buckets.append(ah._spider_hardness(parsed))
    return buckets


def question_index(question_id: object) -> int | None:
    match = re.search(r"(\d+)$", str(question_id))
    return int(match.group(1)) if match else None


def aggregate_spider(path: Path, hardness: list[str], condition_id: str | None = None) -> dict[str, float | int]:
    values: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    with path.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if condition_id is not None and row.get("condition_id") != condition_id:
                continue
            index = question_index(row.get("question_id"))
            if index is None or index >= len(hardness):
                continue
            bucket = hardness[index]
            values[bucket][1] += 1
            values[bucket][0] += 1 if row.get("execution_match") else 0
    out: dict[str, float | int] = {}
    for bucket in ["easy", "medium", "hard", "extra"]:
        correct, total = values.get(bucket, [0, 0])
        out[bucket] = round(100 * correct / total, 2) if total else 0.0
    correct_all = sum(value[0] for value in values.values())
    total_all = sum(value[1] for value in values.values())
    out["all"] = round(100 * correct_all / total_all, 2) if total_all else 0.0
    out["n"] = total_all
    return out


def aggregate_bird_sample(path: Path, condition_id: str | None = "SDMC") -> float:
    total = 0
    correct = 0
    with path.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if condition_id is not None and row.get("condition_id") != condition_id:
                continue
            total += 1
            correct += 1 if row.get("execution_match") else 0
    return round(100 * correct / total, 2) if total else 0.0


def table1_rows() -> list[list[str]]:
    table1_csv = PROJECT / "outputs/rq_final_20260608_023504/results_collected/final_tables_audit/rq1_table1_spider_by_hardness_main.csv"
    by_method = {row["method"]: row for row in csv.DictReader(table1_csv.open())}
    bird = REMOTE_SUMMARY["table1_bird"]
    order = ["RAW_SCHEMA", "TiSQL", "DIN-SQL", "MAC-SQL", "RSL-SQL", "DAIL-SQL", "CHESS", "SDMC"]
    rows = []
    for method in order:
        row = by_method[method]
        rows.append(
            [
                method,
                row["easy"],
                row["medium"],
                row["hard"],
                row["extra"],
                row["all"],
                fmt(bird[method]),
            ]
        )
    return rows


def usage_from_predictions(path: Path) -> dict[str, float | None]:
    if not path.exists():
        return {"input": None, "output": None, "total": None}
    rows = read_jsonl(path)
    prompt = []
    completion = []
    total = []
    for row in rows:
        usage = row.get("usage") or {}
        if isinstance(usage.get("prompt_tokens"), (int, float)):
            prompt.append(float(usage["prompt_tokens"]))
        if isinstance(usage.get("completion_tokens"), (int, float)):
            completion.append(float(usage["completion_tokens"]))
        if isinstance(usage.get("total_tokens"), (int, float)):
            total.append(float(usage["total_tokens"]))
    return {
        "input": sum(prompt) / len(prompt) if prompt else None,
        "output": sum(completion) / len(completion) if completion else None,
        "total": sum(total) / len(total) if total else None,
    }


def aggregate_json_value(path: Path, key: str) -> float | None:
    if not path.exists():
        return None
    return load_json(path).get(key)


def table2_rows(hardness: list[str]) -> tuple[list[list[str]], list[dict[str, object]]]:
    base = PROJECT / "outputs"
    models = []

    def add_model(
        name: str,
        spider_path: Path,
        bird_value: float | str,
        condition: str | None,
        latency: float | None,
        cost: float,
        note: str = "",
        token_stats: dict[str, float | None] | None = None,
    ):
        sp = aggregate_spider(spider_path, hardness, condition) if spider_path.exists() else {
            "easy": "---", "medium": "---", "hard": "---", "extra": "---", "all": "---", "n": 0
        }
        models.append(
            {
                "name": name,
                "spider": sp,
                "bird": bird_value,
                "latency": latency if latency is not None else 0.0,
                "cost": cost,
                "note": note,
                "tokens": token_stats or usage_from_predictions(spider_path.with_name("predictions.jsonl")),
            }
        )

    # Local/API full rows.
    flash = load_csv_row(PROJECT / "outputs/rq_final_20260608_023504/rq1_table2_deepseek_v4flash_spider/aggregate_results.csv", "SDMC")
    pro = load_csv_row(PROJECT / "outputs/rq_final_20260608_023504/rq1_table2_deepseek_v4pro_spider/aggregate_results.csv", "SDMC")
    flash_cost = (float(flash["avg_real_input_tokens"]) * 0.14 + float(flash["avg_real_output_tokens"]) * 0.28) / 1_000_000
    pro_cost = (float(pro["avg_real_input_tokens"]) * 0.435 + float(pro["avg_real_output_tokens"]) * 0.87) / 1_000_000

    qwen_spider = base / "rq_final_20260608_023504/rq1_table2_qwen25_14b_sdmc_spider_full_v2"
    qwen_bird = base / "rq_final_20260608_023504/rq1_table2_qwen25_14b_sdmc_bird_full_v3"
    qwen_bird_value: float | str = "---"
    if (qwen_bird / "aggregate.json").exists():
        qwen_bird_value = round(100 * load_json(qwen_bird / "aggregate.json")["local_execution_match"], 2)
    add_model(
        "Qwen2.5-14B",
        qwen_spider / "executions.jsonl",
        qwen_bird_value,
        "SDMC",
        aggregate_json_value(qwen_spider / "aggregate.json", "avg_generation_latency_seconds"),
        0.0,
        "" if (qwen_spider / "aggregate.json").exists() else "rerun pending",
        usage_from_predictions(qwen_spider / "predictions.jsonl"),
    )
    add_model(
        "Llama3-8B",
        base / "rq_final_20260608_023504/rq1_table2_llama3_8b_sdmc_spider_clean/executions.jsonl",
        round(100 * load_json(base / "rq_final_20260608_023504/rq1_table2_llama3_8b_sdmc_bird_clean/aggregate.json")["local_execution_match"], 2),
        "SDMC",
        load_json(base / "rq_final_20260608_023504/rq1_table2_llama3_8b_sdmc_spider_clean/aggregate.json")["avg_generation_latency_seconds"],
        0.0,
        "",
        usage_from_predictions(base / "rq_final_20260608_023504/rq1_table2_llama3_8b_sdmc_spider_clean/predictions.jsonl"),
    )
    add_model(
        "DS V4 Flash",
        base / "rq_final_20260608_023504/rq1_table2_deepseek_v4flash_spider/executions.jsonl",
        load_csv_row(base / "rq_final_20260608_023504/rq1_table2_deepseek_v4flash_bird/aggregate_results.csv", "SDMC")["execution_match_pct"],
        "SDMC",
        float(flash["avg_generation_latency_seconds"]),
        flash_cost,
        "",
        {"input": float(flash["avg_real_input_tokens"]), "output": float(flash["avg_real_output_tokens"]), "total": float(flash["avg_real_input_tokens"]) + float(flash["avg_real_output_tokens"])},
    )
    add_model(
        "DS V4 Pro",
        base / "rq_final_20260608_023504/rq1_table2_deepseek_v4pro_spider/executions.jsonl",
        load_csv_row(base / "rq_final_20260608_023504/rq1_table2_deepseek_v4pro_bird/aggregate_results.csv", "SDMC")["execution_match_pct"],
        "SDMC",
        float(pro["avg_generation_latency_seconds"]),
        pro_cost,
        "",
        {"input": float(pro["avg_real_input_tokens"]), "output": float(pro["avg_real_output_tokens"]), "total": float(pro["avg_real_input_tokens"]) + float(pro["avg_real_output_tokens"])},
    )
    add_model(
        "Gemma3-12B†",
        base / "current_experiments/gemma3_spider_full/executions.jsonl",
        f"{aggregate_bird_sample(base / 'current_experiments/gemma3_bird_sample/executions.jsonl', 'SDMC'):.2f}†",
        "SDMC",
        load_json(base / "current_experiments/gemma3_spider_full/aggregate.json")["avg_generation_latency_seconds"],
        0.0,
        "BIRD is sampled-700",
        usage_from_predictions(base / "current_experiments/gemma3_spider_full/predictions.jsonl"),
    )
    add_model(
        "Gemma4-26B",
        base / "rq_final_20260608_023504/rq1_table2_gemma4_26b_sdmc_spider_clean/executions.jsonl",
        round(100 * load_json(base / "rq_final_20260608_023504/rq1_table2_gemma4_26b_sdmc_bird_clean/aggregate.json")["local_execution_match"], 2),
        "SDMC",
        load_json(base / "rq_final_20260608_023504/rq1_table2_gemma4_26b_sdmc_spider_clean/aggregate.json")["avg_generation_latency_seconds"],
        0.0,
        "",
        usage_from_predictions(base / "rq_final_20260608_023504/rq1_table2_gemma4_26b_sdmc_spider_clean/predictions.jsonl"),
    )

    rows = []
    for model in models:
        sp = model["spider"]
        rows.append(
            [
                str(model["name"]),
                fmt(sp["easy"]),
                fmt(sp["medium"]),
                fmt(sp["hard"]),
                fmt(sp["extra"]),
                fmt(sp["all"]),
                fmt(model["bird"]),
            ]
        )
    return rows, models


def row_from_summary(name: str, row: dict[str, object]) -> list[str]:
    return [name, fmt(row["easy"]), fmt(row["medium"]), fmt(row["hard"]), fmt(row["extra"]), fmt(row["all"]), fmt(row["bird"])]


def table_from_summary(section: str, order: list[str]) -> list[list[str]]:
    return [row_from_summary(name, REMOTE_SUMMARY[section][name]) for name in order]


def latest_manifest(root: Path) -> dict | None:
    manifests = sorted(root.glob("*/manifest.json"))
    if not manifests:
        return None
    return load_json(manifests[-1])


def rq2_cost_rows() -> list[list[str]]:
    base = PROJECT / "outputs/rq_final_20260608_023504"
    static = {
        "Spider": {
            "store_size_mb": 48246784 / (1024 * 1024),
            "databases": 166,
            "context_items": 5536,
            "graph_nodes": 29527,
            "graph_edges": 30908,
            "inventory_seconds": None,
            "build_graph_seconds": None,
        },
        "BIRD": {
            "store_size_mb": 8695808 / (1024 * 1024),
            "databases": 11,
            "context_items": 884,
            "graph_nodes": 5190,
            "graph_edges": 5360,
            "inventory_seconds": None,
            "build_graph_seconds": None,
        },
    }
    spider_manifest = latest_manifest(base / "store_graph_cost_benchmark_<gpu-alias>_cpu")
    if spider_manifest:
        s = spider_manifest["summaries"][0]
        static["Spider"]["inventory_seconds"] = s["inventory"]["elapsed_seconds"]
        static["Spider"]["build_graph_seconds"] = s["build_materialize_graph"]["elapsed_seconds"]
    bird_manifest = latest_manifest(base / "store_graph_cost_benchmark")
    if bird_manifest:
        for s in bird_manifest["summaries"]:
            if s["dataset"] == "bird":
                static["BIRD"]["inventory_seconds"] = s["inventory"]["elapsed_seconds"]
                static["BIRD"]["build_graph_seconds"] = s["build_materialize_graph"]["elapsed_seconds"]
    rows = []
    for dataset in ["Spider", "BIRD"]:
        row = static[dataset]
        rows.append([
            dataset,
            fmt(row["databases"]),
            fmt(row["context_items"]),
            fmt(row["graph_nodes"]),
            fmt(row["graph_edges"]),
            f'{float(row["store_size_mb"]):.2f}',
            fmt(row["inventory_seconds"]),
            fmt(row["build_graph_seconds"]),
        ])
    return rows


def rq3_generation_speed_rows() -> list[list[str]]:
    roots = sorted((PROJECT / "outputs/rq_final_20260608_023504/rq3_context_generation_speed").glob("*/rq3_context_generation_speed.csv"))
    if not roots:
        return [["---", "---", "---", "---", "---", "---", "---", "---"]]
    rows = list(csv.DictReader(roots[-1].open(encoding="utf-8")))
    out = []
    for row in rows:
        out.append([
            row["context_type"],
            row["dataset"],
            row["scope"],
            row["databases"],
            row["api_calls"],
            fmt(float(row["elapsed_seconds"])),
            fmt(float(row["seconds_per_database"])) if row["seconds_per_database"] else "---",
            f'{float(row["output_bytes"]) / (1024 * 1024):.2f}',
        ])
    return out


def create_token_svg(models: list[dict[str, object]]) -> str:
    width, height = 980, 460
    left, top = 150, 40
    chart_w, chart_h = 740, 320
    totals = [float((m.get("tokens") or {}).get("total") or 0) for m in models]
    max_tokens = max(totals) * 1.15 or 1.0
    bar_gap = chart_w / len(models)
    token_color = "#2f6fdd"
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<text x="30" y="28" font-family="Arial" font-size="18" font-weight="700">RQ5 Token Consumption for SDMC Backbones</text>',
        f'<line x1="{left}" y1="{top+chart_h}" x2="{left+chart_w}" y2="{top+chart_h}" stroke="#333"/>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top+chart_h}" stroke="#333"/>',
        f'<text x="{left+chart_w-260}" y="{top+20}" font-family="Arial" font-size="13" fill="{token_color}">Average total tokens per query</text>',
    ]
    for idx, model in enumerate(models):
        x = left + idx * bar_gap + 16
        tokens = float((model.get("tokens") or {}).get("total") or 0)
        h1 = chart_h * tokens / max_tokens
        parts.append(f'<rect x="{x:.1f}" y="{top+chart_h-h1:.1f}" width="38" height="{h1:.1f}" fill="{token_color}"/>')
        parts.append(f'<text x="{x-8:.1f}" y="{top+chart_h+18}" font-family="Arial" font-size="11" transform="rotate(35 {x-8:.1f},{top+chart_h+18})">{escape(str(model["name"]))}</text>')
        if tokens > 0:
            parts.append(f'<text x="{x-2:.1f}" y="{top+chart_h-h1-5:.1f}" font-family="Arial" font-size="10" fill="{token_color}">{tokens:.0f}</text>')
    parts.append('</svg>')
    return "\n".join(parts)


def w_text(text: object, bold: bool = False, size: int = 18) -> str:
    val = escape(str(text))
    b = "<w:b/>" if bold else ""
    return f'<w:r><w:rPr>{b}<w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr><w:t xml:space="preserve">{val}</w:t></w:r>'


def paragraph(text: str = "", style: str | None = None, bold: bool = False, size: int = 18) -> str:
    ppr = ""
    if style:
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>'
    return f"<w:p>{ppr}{w_text(text, bold=bold, size=size)}</w:p>"


def table_xml(headers: list[str], rows: list[list[str]]) -> str:
    def cell(text: str, bold: bool = False) -> str:
        return (
            '<w:tc><w:tcPr><w:tcW w:w="1800" w:type="dxa"/></w:tcPr>'
            f'<w:p><w:pPr><w:spacing w:after="0"/></w:pPr>{w_text(text, bold=bold, size=16)}</w:p></w:tc>'
        )

    table = [
        '<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" w:type="auto"/>'
        '<w:tblLook w:firstRow="1" w:lastRow="0" w:firstColumn="0" w:lastColumn="0" w:noHBand="0" w:noVBand="1"/></w:tblPr>'
    ]
    table.append("<w:tr>" + "".join(cell(h, True) for h in headers) + "</w:tr>")
    for row in rows:
        table.append("<w:tr>" + "".join(cell(str(v)) for v in row) + "</w:tr>")
    table.append("</w:tbl>")
    return "".join(table)


def image_paragraph(rel_id: str, cx: int = 8500000, cy: int = 4000000) -> str:
    return f'''<w:p><w:r><w:drawing><wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="1" name="RQ5 Figure"/><a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:nvPicPr><pic:cNvPr id="0" name="rq5_latency_cost.svg"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:embed="{rel_id}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>'''


def build_docx(table2_models: list[dict[str, object]], body_parts: list[str]) -> None:
    svg = create_token_svg(table2_models).encode("utf-8")
    document_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<w:body>
{''.join(body_parts)}
<w:sectPr><w:pgSz w:w="15840" w:h="12240" w:orient="landscape"/><w:pgMar w:top="720" w:right="720" w:bottom="720" w:left="720" w:header="450" w:footer="450" w:gutter="0"/></w:sectPr>
</w:body></w:document>'''
    styles_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:rPr><w:b/><w:sz w:val="24"/></w:rPr></w:style><w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:tblPr><w:tblBorders><w:top w:val="single" w:sz="4"/><w:left w:val="single" w:sz="4"/><w:bottom w:val="single" w:sz="4"/><w:right w:val="single" w:sz="4"/><w:insideH w:val="single" w:sz="4"/><w:insideV w:val="single" w:sz="4"/></w:tblBorders></w:tblPr></w:style></w:styles>'''
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="svg" ContentType="image/svg+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>'''
    root_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'''
    doc_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rIdImg1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/rq5_token_consumption.svg"/><Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'''
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("word/styles.xml", styles_xml)
        zf.writestr("word/_rels/document.xml.rels", doc_rels)
        zf.writestr("word/media/rq5_token_consumption.svg", svg)


def main() -> None:
    hardness = build_hardness()
    headers = ["Method", "Spider Easy", "Spider Medium", "Spider Hard", "Spider Extra", "Spider All", "BIRD Dev EX"]
    table2, models = table2_rows(hardness)
    body: list[str] = []
    body.append(paragraph("SDMC Experiment Results", "Title"))
    body.append(paragraph("RQ1. Overall Performance and Model Backbone", "Heading1"))
    body.append(paragraph("Table 1. Overall performance under Gemma4-26B.", bold=True))
    body.append(table_xml(headers, table1_rows()))
    body.append(paragraph("Table 2. SDMC with different LLM backbones.", bold=True))
    body.append(table_xml(["Backbone"] + headers[1:], table2))
    body.append(paragraph("* Qwen2.5-14B uses the clean full-dev rerun for both Spider and BIRD. † Gemma3 BIRD uses sampled-700.", size=14))

    body.append(paragraph("RQ2. Context Store and Context Graph", "Heading1"))
    body.append(paragraph("Table 3. Context Store / Context Graph mechanism.", bold=True))
    body.append(table_xml(["Variant"] + headers[1:], table_from_summary("rq2", ["RAW_SCHEMA", "SDMC (Store + Graph)", "SDMC_FLAT_STORE", "SDMC_FULL", "SDMC_GRAPH_NO_REL", "SDMC_GRAPH_SCHEMA_ONLY"])))
    body.append(paragraph("Table 3b. Context Store / Context Graph construction cost.", bold=True))
    body.append(table_xml(["Dataset", "DBs", "Context items", "Graph nodes", "Graph edges", "Store MB", "Inventory sec", "Build+Graph sec"], rq2_cost_rows()))

    body.append(paragraph("RQ3. Different Context Generation for Text-to-SQL", "Heading1"))
    body.append(paragraph("Table 4. Schema-based, LLM-based, and SQL-based context across methods.", bold=True))
    rq3_rows = []
    for method in ["MAC-SQL", "TiSQL", "SDMC"]:
        for ctx in ["Schema-based", "LLM-based", "SQL-based"]:
            row = REMOTE_SUMMARY["rq3"][f"{method}|{ctx}"]
            rq3_rows.append([method, ctx, fmt(row["easy"]), fmt(row["medium"]), fmt(row["hard"]), fmt(row["extra"]), fmt(row["all"]), fmt(row["bird"])])
    body.append(table_xml(["Method", "Context"] + headers[1:], rq3_rows))
    body.append(paragraph("Table 4b. Context generation speed by context type.", bold=True))
    body.append(table_xml(["Context type", "Dataset", "Scope", "DBs", "API calls", "Elapsed sec", "Sec/DB", "Output MB"], rq3_generation_speed_rows()))
    body.append(paragraph("Note: LLM-based rows are controlled sampled generation because historical full HDC files do not preserve auditable generation timing logs.", size=14))

    body.append(paragraph("RQ4. Three-Level Context Ablation", "Heading1"))
    body.append(paragraph("Table 5. Column/table/database context ablation.", bold=True))
    body.append(table_xml(["Variant"] + headers[1:], table_from_summary("rq4", ["SDMC full", "w/o Column Context", "w/o Table Context", "w/o Database Context", "only Column Context", "only Table Context", "only Database Context"])))

    body.append(paragraph("RQ5. Latency and Cost", "Heading1"))
    body.append(paragraph("Figure 1. Token consumption of Table 2 backbones. Cost is reported in the table as an auxiliary field; local models have API cost 0.", size=14))
    body.append(image_paragraph("rIdImg1"))
    body.append(table_xml(
        ["Backbone", "Input tok/q", "Output tok/q", "Total tok/q", "Latency (s/query)", "API cost (USD/query)", "Note"],
        [[
            str(m["name"]),
            fmt((m.get("tokens") or {}).get("input")),
            fmt((m.get("tokens") or {}).get("output")),
            fmt((m.get("tokens") or {}).get("total")),
            f'{float(m["latency"]):.3f}',
            f'{float(m["cost"]):.6f}',
            str(m.get("note", "")),
        ] for m in models],
    ))
    build_docx(models, body)
    print(OUTPUT)


if __name__ == "__main__":
    main()
