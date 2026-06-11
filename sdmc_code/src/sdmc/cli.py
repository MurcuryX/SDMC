from __future__ import annotations

from pathlib import Path
import argparse
import json
import sys

from sdmc.config import load_config
from sdmc.experiment import ExperimentSpec, run_experiment
from sdmc.graph import materialize_graphs
from sdmc.hdc import generate_hdc_for_database
from sdmc.reports import aggregate_experiment, paired_analysis, stage_a_sanity
from sdmc.stage_a import run_build, run_inventory
from sdmc.stage_b import DeepSeekAdapter, dry_run_question


def default_root(config, dataset: str) -> str:
    if dataset.lower() == "spider":
        return config.spider_full_path
    if dataset.lower() == "bird":
        return config.bird_full_path
    raise ValueError(f"unsupported dataset: {dataset}")


def cmd_inventory(args) -> int:
    config = load_config(args.config)
    root = args.root or default_root(config, args.dataset)
    out = args.output or Path(config.output_root) / "context_build" / args.dataset / args.split
    inventories = run_inventory(args.dataset, args.split, root, out, config)
    print(json.dumps({"status": "ok", "databases": len(inventories), "output": str(out)}, ensure_ascii=False))
    return 0


def cmd_build(args) -> int:
    config = load_config(args.config)
    root = args.root or default_root(config, args.dataset)
    out = args.output or Path(config.output_root) / "context_build" / args.dataset / args.split
    run_build(args.dataset, args.split, root, out, config, limit=args.limit, force=args.force)
    if args.materialize_graph:
        materialize_graphs(Path(out) / "context_store.sqlite")
    print(json.dumps({"status": "ok", "output": str(out), "limit": args.limit}, ensure_ascii=False))
    return 0


def cmd_materialize_graph(args) -> int:
    materialize_graphs(args.store, args.database_id)
    print(json.dumps({"status": "ok", "store": args.store, "database_id": args.database_id}, ensure_ascii=False))
    return 0


def cmd_dry_run_question(args) -> int:
    config = load_config(args.config)
    result = dry_run_question(args.store, args.database_id, args.question, config)
    out = Path(args.output) if args.output else None
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"status": "ok", "output": str(out)}, ensure_ascii=False))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_generate(args) -> int:
    config = load_config(args.config)
    prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    adapter = DeepSeekAdapter(config, args.api_key_file)
    result = adapter.generate(prompt, allow_api_calls=args.allow_api_calls)
    safe = {k: v for k, v in result.items() if k != "raw_response"}
    if args.output:
        Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(safe, ensure_ascii=False))
    return 0 if result.get("status") in {"success", "blocked_no_api_calls"} else 2


def cmd_run_experiment(args) -> int:
    config = load_config(args.config)
    spec = ExperimentSpec(
        dataset=args.dataset,
        split=args.split,
        root=args.root or default_root(config, args.dataset),
        store=args.store,
        output_dir=args.output,
        conditions=args.conditions.split(","),
        limit=args.limit,
        sample=args.sample,
        seed=args.seed,
        api_key_file=args.api_key_file,
        hdc_store=args.hdc_store,
    )
    result = run_experiment(spec, config, allow_api_calls=args.allow_api_calls, dry_run=not args.real_run)
    print(json.dumps(result, ensure_ascii=False))
    return 0


def cmd_hdc_generate(args) -> int:
    config = load_config(args.config)
    result = generate_hdc_for_database(args.store, args.hdc_store, args.database_id, config, args.api_key_file, allow_api_calls=args.allow_api_calls)
    print(json.dumps(result, ensure_ascii=False))
    return 0


def cmd_report(args) -> int:
    if args.kind == "stage-a":
        rows = stage_a_sanity(args.output_root)
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    elif args.kind == "aggregate":
        print(json.dumps(aggregate_experiment(args.output), ensure_ascii=False, indent=2))
    elif args.kind == "paired":
        print(json.dumps(paired_analysis(args.output, args.baseline, args.ours), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sdmc", description="SDMC Stage A/B tooling")
    p.add_argument("--config", default="configs/sdmc_default.yaml")
    sub = p.add_subparsers(dest="command", required=True)

    inv = sub.add_parser("inventory", help="Build dataset inventory and manifest")
    inv.add_argument("--dataset", required=True, choices=["spider", "bird"])
    inv.add_argument("--split", required=True)
    inv.add_argument("--root")
    inv.add_argument("--output")
    inv.set_defaults(func=cmd_inventory)

    build = sub.add_parser("build", help="Build Context Store for a dataset split")
    build.add_argument("--dataset", required=True, choices=["spider", "bird"])
    build.add_argument("--split", required=True)
    build.add_argument("--root")
    build.add_argument("--output")
    build.add_argument("--limit", type=int)
    build.add_argument("--materialize-graph", action="store_true")
    build.add_argument("--force", action="store_true")
    build.set_defaults(func=cmd_build)

    graph = sub.add_parser("materialize-graph", help="Materialize graph tables from Context Store")
    graph.add_argument("--store", required=True)
    graph.add_argument("--database-id")
    graph.set_defaults(func=cmd_materialize_graph)

    dry = sub.add_parser("dry-run-question", help="Build C0/C1/SDMC prompts without model calls")
    dry.add_argument("--store", required=True)
    dry.add_argument("--database-id", required=True)
    dry.add_argument("--question", required=True)
    dry.add_argument("--output")
    dry.set_defaults(func=cmd_dry_run_question)

    gen = sub.add_parser("generate", help="Call configured model for a prompt file")
    gen.add_argument("--prompt-file", required=True)
    gen.add_argument("--api-key-file")
    gen.add_argument("--allow-api-calls", action="store_true")
    gen.add_argument("--output")
    gen.set_defaults(func=cmd_generate)

    exp = sub.add_parser("run-experiment", help="Run or dry-run batch Stage B experiment")
    exp.add_argument("--dataset", required=True, choices=["spider", "bird"])
    exp.add_argument("--split", required=True)
    exp.add_argument("--root")
    exp.add_argument("--store", required=True)
    exp.add_argument("--output", required=True)
    exp.add_argument("--conditions", required=True, help="Comma-separated conditions, e.g. RAW_SCHEMA,SDMC")
    exp.add_argument("--limit", type=int)
    exp.add_argument("--sample", type=int)
    exp.add_argument("--seed", type=int, default=13)
    exp.add_argument("--api-key-file")
    exp.add_argument("--hdc-store")
    exp.add_argument("--allow-api-calls", action="store_true")
    exp.add_argument("--real-run", action="store_true", help="Actually call model/evaluate; omitted means dry run")
    exp.set_defaults(func=cmd_run_experiment)

    hdc = sub.add_parser("hdc-generate", help="Generate HDC-style context for one database")
    hdc.add_argument("--store", required=True)
    hdc.add_argument("--hdc-store", required=True)
    hdc.add_argument("--database-id", required=True)
    hdc.add_argument("--api-key-file")
    hdc.add_argument("--allow-api-calls", action="store_true")
    hdc.set_defaults(func=cmd_hdc_generate)

    rep = sub.add_parser("report", help="Generate reports")
    rep.add_argument("--kind", required=True, choices=["stage-a", "aggregate", "paired"])
    rep.add_argument("--output-root", default="outputs")
    rep.add_argument("--output")
    rep.add_argument("--baseline", default="RAW_SCHEMA")
    rep.add_argument("--ours", default="SDMC")
    rep.set_defaults(func=cmd_report)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
