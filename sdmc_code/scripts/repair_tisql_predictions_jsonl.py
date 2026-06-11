from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

from tiinsight_repro.datasets import load_examples


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--dataset", required=True, choices=["spider", "bird"])
    parser.add_argument("--split", default="dev")
    parser.add_argument("--backup-suffix", default="")
    args = parser.parse_args()

    path = Path(args.predictions)
    examples = {str(ex.question_id): ex for ex in load_examples(args.dataset, args.split)}
    rows: dict[str, dict] = {}
    bad_lines: list[dict] = []

    lines = path.read_text(encoding="utf-8").splitlines()
    for lineno, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception as exc:
            bad_lines.append(
                {
                    "lineno": lineno,
                    "error": repr(exc),
                    "prefix": line[:300],
                }
            )
            continue
        qid = str(row.get("question_id"))
        if qid in examples:
            rows[qid] = row
        else:
            bad_lines.append(
                {
                    "lineno": lineno,
                    "error": f"unknown question_id={qid!r}",
                    "prefix": line[:300],
                }
            )

    missing = [qid for qid in examples if qid not in rows]
    for qid in missing:
        ex = examples[qid]
        rows[qid] = {
            "db_id": ex.db_id,
            "question": ex.question,
            "evidence": ex.evidence,
            "sql": "SELECT 1 WHERE 0;",
            "selected_schema": {},
            "trace": [
                {
                    "stage": "repair_missing_prediction",
                    "reason": "corrupt or missing JSONL row after completed generation",
                }
            ],
            "question_id": ex.question_id,
            "gold_sql": ex.gold_sql,
            "difficulty": ex.difficulty,
        }

    suffix = args.backup_suffix or time.strftime(".corrupt_%Y%m%d_%H%M%S.bak")
    backup = path.with_name(path.name + suffix)
    shutil.copy2(path, backup)

    ordered = sorted(rows.values(), key=lambda row: int(row["question_id"]) if str(row["question_id"]).isdigit() else str(row["question_id"]))
    with path.open("w", encoding="utf-8") as f:
        for row in ordered:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    report = {
        "path": str(path),
        "backup": str(backup),
        "input_lines": len(lines),
        "valid_rows_before_repair": len(rows) - len(missing),
        "bad_line_count": len(bad_lines),
        "missing_filled": len(missing),
        "final_rows": len(ordered),
        "bad_lines": bad_lines[:20],
        "missing_question_ids": missing[:50],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
