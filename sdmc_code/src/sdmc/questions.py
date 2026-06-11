from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import random

from sdmc.datasets import resolve_bird_split_root


@dataclass(frozen=True)
class QuestionExample:
    question_id: str
    dataset_name: str
    split_name: str
    database_id: str
    question: str
    gold_sql: str | None
    difficulty: str | None = None
    evidence: str | None = None


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_spider_questions(root: str | Path, split: str) -> list[QuestionExample]:
    root = Path(root)
    files = {
        "dev": ["dev.json"],
        "train": ["train_spider.json", "train_others.json"],
        "test": ["test.json"],
    }.get(split, [f"{split}.json"])
    out: list[QuestionExample] = []
    idx = 0
    for name in files:
        path = root / name
        if not path.exists():
            continue
        for row in _load_json(path):
            out.append(QuestionExample(
                question_id=f"spider-{split}-{idx}",
                dataset_name="spider",
                split_name=split,
                database_id=row["db_id"],
                question=row["question"],
                gold_sql=row.get("query"),
                difficulty=None,
                evidence=None,
            ))
            idx += 1
    return out


def load_bird_questions(root: str | Path, split: str) -> list[QuestionExample]:
    root = resolve_bird_split_root(root, split)
    path = root / f"{split}.json"
    if not path.exists():
        raise FileNotFoundError(path)
    rows = _load_json(path)
    out: list[QuestionExample] = []
    for idx, row in enumerate(rows):
        qid = str(row.get("question_id", f"bird-{split}-{idx}"))
        out.append(QuestionExample(
            question_id=qid,
            dataset_name="bird",
            split_name=split,
            database_id=row["db_id"],
            question=row["question"],
            gold_sql=row.get("SQL"),
            difficulty=row.get("difficulty"),
            evidence=row.get("evidence"),
        ))
    return out


def load_questions(dataset: str, split: str, root: str | Path, limit: int | None = None, sample: int | None = None, seed: int = 13) -> list[QuestionExample]:
    if dataset == "spider":
        rows = load_spider_questions(root, split)
    elif dataset == "bird":
        rows = load_bird_questions(root, split)
    else:
        raise ValueError(f"unsupported dataset: {dataset}")
    if sample is not None and sample < len(rows):
        rng = random.Random(seed)
        rows = rng.sample(rows, sample)
        rows.sort(key=lambda r: r.question_id)
    if limit is not None:
        rows = rows[:limit]
    return rows


def question_to_json(q: QuestionExample, include_forbidden: bool = False) -> dict[str, Any]:
    data = {
        "question_id": q.question_id,
        "dataset_name": q.dataset_name,
        "split_name": q.split_name,
        "database_id": q.database_id,
        "question": q.question,
        "difficulty": q.difficulty,
    }
    if include_forbidden:
        data["gold_sql"] = q.gold_sql
        data["evidence"] = q.evidence
    return data
