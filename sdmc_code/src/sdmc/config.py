from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json
import os
import re

try:
    import yaml
except Exception:  # pragma: no cover - PyYAML exists on this host, JSON still works.
    yaml = None


@dataclass(frozen=True)
class ProfilingBudget:
    max_database_build_seconds: float = 1800.0
    max_table_build_seconds: float = 300.0
    max_column_profile_seconds: float = 30.0
    max_topk_columns_per_table: int = 30
    max_topk_columns_per_database: int = 500
    max_distinct_before_topk: int = 200
    topk_limit: int = 5
    sample_limit: int = 50


@dataclass(frozen=True)
class StageBConfig:
    prompt_budget_tokens: int = 8000
    max_selected_tables: int = 12
    max_selected_columns: int = 120
    max_value_encoding_nodes: int = 40
    max_statistic_nodes: int = 80
    max_relationship_edges: int = 80
    max_context_items: int = 160
    use_bm25: bool = True
    use_embeddings: bool = False
    temperature: float = 0.0
    max_output_tokens: int = 1024
    prompt_style: str = "direct"
    enable_explain_repair: bool = False
    enable_runtime_repair: bool = False
    max_repair_attempts: int = 1
    model: str = "deepseek-v4-pro"
    endpoint: str = "https://api.deepseek.com"
    thinking: str = "disabled"
    max_api_calls_per_run: int = 100
    max_prompt_tokens_for_api: int = 12000


@dataclass(frozen=True)
class SDMCConfig:
    schema_version: str = "sdmc-store-v1"
    sdmc_version: str = "0.1.0"
    spider_full_path: str = "<SERVER1_DATA_ROOT>/share_data/text_to_sql/spider_full/extracted/spider_data"
    bird_full_path: str = "<SERVER1_DATA_ROOT>/share_data/text_to_sql/bird_full"
    output_root: str = "outputs"
    profiling: ProfilingBudget = field(default_factory=ProfilingBudget)
    stage_b: StageBConfig = field(default_factory=StageBConfig)


def _merge_dataclass(default: Any, data: dict[str, Any]) -> Any:
    values = {}
    for name, field_info in default.__dataclass_fields__.items():
        value = getattr(default, name)
        if name not in data:
            values[name] = value
        elif hasattr(value, "__dataclass_fields__"):
            values[name] = _merge_dataclass(value, data[name] or {})
        else:
            values[name] = data[name]
    return type(default)(**values)


def load_config(path: str | Path | None = None) -> SDMCConfig:
    default = SDMCConfig()
    if path is None:
        return default
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in {".yaml", ".yml"} and yaml is not None:
        data = yaml.safe_load(text) or {}
    else:
        data = json.loads(text)
    return _merge_dataclass(default, data)


def read_api_key(api_key_file: str | Path | None = None, env_name: str = "DEEPSEEK_API_KEY") -> str | None:
    key = os.environ.get(env_name)
    if key:
        return key.strip()
    if api_key_file is None:
        return None
    p = Path(api_key_file)
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8")
    sk_match = re.search(r"sk-[A-Za-z0-9_-]+", text)
    if sk_match:
        return sk_match.group(0)
    quoted = re.search(r"api_key\s*=\s*['\"]([^'\"]+)['\"]", text)
    if quoted:
        return quoted.group(1).strip()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            _, value = stripped.split("=", 1)
            value = value.strip().strip("\"'")
            if value:
                return value
        elif stripped:
            return stripped.strip("\"'")
    return None
