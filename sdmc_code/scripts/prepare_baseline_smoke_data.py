#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Create small dev JSON files for baseline smoke runs.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()

    src = Path(args.input)
    dst = Path(args.output)
    rows = json.loads(src.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"expected list JSON in {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(rows[: args.limit], ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "ok", "input": str(src), "output": str(dst), "rows": min(args.limit, len(rows))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
