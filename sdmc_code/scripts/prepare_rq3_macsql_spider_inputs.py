#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sdmc.config import load_config
from sdmc.experiment import condition_prompt
from sdmc.hdc import HDCStore
from sdmc.questions import load_questions
from sdmc.stage_b import StageBEngine


CONDITION_MAP = {
    "schema": "RAW_SCHEMA",
    "llm": "HDC_STYLE",
    "sql": "SDMC",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build MAC-SQL inputs with RQ3 context injected into the question text.")
    parser.add_argument("--dataset", choices=["spider", "bird"], default="spider")
    parser.add_argument("--root", required=True)
    parser.add_argument("--store", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--hdc-store")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--contexts",
        default="schema,llm,sql",
        help="Comma-separated context kinds to generate: schema,llm,sql.",
    )
    parser.add_argument(
        "--max-rendered-context-chars",
        type=int,
        default=0,
        help="If positive, cap rendered context before injecting it into the MAC-SQL question.",
    )
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    root = Path(args.root)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_rows = json.loads((root / "dev.json").read_text(encoding="utf-8"))
    questions = load_questions(args.dataset, "dev", root, limit=args.limit)
    by_index = {idx: row for idx, row in enumerate(raw_rows)}

    config = load_config(args.config)
    engine = StageBEngine(args.store, config)
    hdc = HDCStore(args.hdc_store) if args.hdc_store else None
    context_names = [name.strip() for name in args.contexts.split(",") if name.strip()]
    unknown = [name for name in context_names if name not in CONDITION_MAP]
    if unknown:
        raise ValueError(f"unknown context kinds: {unknown}")
    if "llm" in context_names and hdc is None:
        raise ValueError("--hdc-store is required when generating llm context")
    try:
        for context_name in context_names:
            condition = CONDITION_MAP[context_name]
            out_rows = []
            audit_rows = []
            for idx, q in enumerate(questions):
                row = dict(by_index[idx])
                pack = condition_prompt(engine, q, condition, config, hdc)
                rendered = pack["rendered_context"]
                if args.max_rendered_context_chars and len(rendered) > args.max_rendered_context_chars:
                    rendered = rendered[: args.max_rendered_context_chars].rstrip() + "\n[Context truncated for MAC-SQL prompt budget.]"
                warning = ";".join(pack["condition_warnings"])
                prefix = (
                    f"[RQ3 {context_name.upper()} CONTEXT]\n"
                    f"{rendered}\n\n"
                    "[Question]\n"
                )
                row["question"] = prefix + row["question"]
                out_rows.append(row)
                audit_rows.append({
                    "index": idx,
                    "question_id": q.question_id,
                    "db_id": q.database_id,
                    "context_name": context_name,
                    "condition": condition,
                    "warning": warning,
                    "rendered_context_chars": len(rendered),
                    "estimated_input_tokens": pack["estimated_input_tokens"],
                })
            out_path = output_dir / f"macsql_{args.dataset}_rq3_{context_name}.json"
            audit_path = output_dir / f"macsql_{args.dataset}_rq3_{context_name}.audit.jsonl"
            out_path.write_text(json.dumps(out_rows, ensure_ascii=False, indent=2), encoding="utf-8")
            with audit_path.open("w", encoding="utf-8") as f:
                for audit in audit_rows:
                    f.write(json.dumps(audit, ensure_ascii=False) + "\n")
            print(json.dumps({"context": context_name, "rows": len(out_rows), "output": str(out_path)}, ensure_ascii=False))
    finally:
        engine.close()
        if hdc:
            hdc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
