# SDMC

SQL-Derived Multi-level Context for Reliable Text-to-SQL.

This directory contains the Python package and experiment wrappers used by the
SDMC paper. It is intended to be used from the repository root:

```bash
cd sdmc
pip install -e .
PYTHONPATH=src python3 -m sdmc --help
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## Main Commands

Inventory a benchmark split:

```bash
PYTHONPATH=src python3 -m sdmc inventory \
  --dataset spider \
  --split dev \
  --root <SPIDER_ROOT> \
  --output outputs/context_build/spider/dev
```

Build SQL-derived context and materialize the graph:

```bash
PYTHONPATH=src python3 -m sdmc build \
  --dataset spider \
  --split dev \
  --root <SPIDER_ROOT> \
  --output outputs/context_build/spider/dev \
  --materialize-graph
```

Run a question-time dry run without model calls:

```bash
PYTHONPATH=src python3 -m sdmc dry-run-question \
  --store outputs/context_build/spider/dev/context_store.sqlite \
  --database-id <db_id> \
  --question "..." \
  --output outputs/stage_b/dry_runs/example.json
```

Run an experiment condition:

```bash
PYTHONPATH=src python3 -m sdmc run-experiment \
  --dataset spider \
  --split dev \
  --root <SPIDER_ROOT> \
  --store outputs/context_build/spider/dev/context_store.sqlite \
  --output outputs/runs/spider_sdmc \
  --conditions SDMC
```

Real API/model calls require explicit flags and credentials configured outside
the repository. Do not commit API keys.

## Reproduction Wrappers

See `scripts/` for paper experiment wrappers. They are templates from the
paper environment and should be edited for local paths, model endpoints, and
available GPUs before execution.
