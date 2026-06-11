# SDMC ICDE Open-Source Artifact

This artifact contains the framework and experiment code for:

**Text2SQL2: SQL-Derived Multi-level Context for Reliable Text-to-SQL**

The package is organized for ICDE-style artifact release. It intentionally excludes API keys, model weights, virtual environments, raw benchmark datasets, generated checkpoints, and full experiment outputs.

## Contents

```text
sdmc/
  src/sdmc/                 Core SDMC package
  scripts/                  Stage A/Stage B/RQ experiment wrappers
  configs/                  Example model and selector configs
  tests/                    Lightweight unit tests
  results_curated/          Curated result tables used for the paper
  pyproject.toml            Python package metadata
docs/
  stage_a_full_dataset_context_construction.md
  stage_b_question_time_selected_sdmc.md
  SOP_RQ*.md                Reproduction SOPs for the experimental RQs
```

## What Is Included

- Stage A: full-dataset context construction, Context Store creation, and Context Graph materialization.
- Stage B: question-time context selection, selected-subgraph rendering, prompt construction, SQL extraction, and local execution matching.
- Experiment scripts for RQ1-RQ5, including baseline preparation wrappers, Store/Graph ablations, context-source comparisons, context-level ablations, cost/latency collection, and selected-subgraph budget sensitivity.
- Curated result tables used for the paper.

## What Is Not Included

- API credentials. Use environment variables or a private key file outside the repository.
- Spider/BIRD datasets. Download them from their official sources.
- HuggingFace model weights. Configure local model endpoints separately.
- Virtual environments and package caches.
- Full raw experiment outputs and generated context stores.
- Vendored copies of third-party baselines. The `scripts/baselines/` directory contains our adapters/wrappers; clone the original baseline repositories according to their own licenses when reproducing those rows.

## Quick Start

```bash
cd sdmc
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
pip install -r ../requirements.txt
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m sdmc --help
```

Most core SDMC code uses the Python standard library. Optional experiment scripts may require additional packages depending on the chosen baseline or local model service.

## Dataset Layout

Set dataset roots explicitly in commands. Typical inputs are:

```text
<SPIDER_ROOT>/
  dev.json
  tables.json
  database/<db_id>/<db_id>.sqlite

<BIRD_ROOT>/
  dev/dev.json
  dev/dev_tables.json
  dev/dev_databases/<db_id>/<db_id>.sqlite
```

## Stage A Example

```bash
PYTHONPATH=src python3 -m sdmc inventory \
  --dataset spider \
  --split dev \
  --root <SPIDER_ROOT> \
  --output outputs/context_build/spider/dev

PYTHONPATH=src python3 -m sdmc build \
  --dataset spider \
  --split dev \
  --root <SPIDER_ROOT> \
  --output outputs/context_build/spider/dev \
  --materialize-graph
```

## Stage B Dry Run

```bash
PYTHONPATH=src python3 -m sdmc dry-run-question \
  --store outputs/context_build/spider/dev/context_store.sqlite \
  --database-id <db_id> \
  --question "How many ...?" \
  --output outputs/stage_b/dry_runs/example.json
```

Dry runs do not call external models.

## Full Experiment Reproduction

The RQ wrappers are in `sdmc/scripts/`. The main families are:

- `run_stage_a_full_v2.sh`: full Context Store / Graph construction.
- `run_rq2_gemma4*.sh`: Store/Graph mechanism ablations.
- `run_rq4_gemma4*.sh`: column/table/database context ablations.
- `run_sensitivity_singlevar_server1.sh`: selected-subgraph budget sensitivity.
- `scripts/baselines/*.sh`: adapters for reproduced baseline rows.

The original scripts were run in a two-machine setting: a CPU/control host for API calls and a GPU/data host for local model serving. Replace all placeholders such as `<SDMC_ROOT>`, `<SERVER1_DATA_ROOT>`, `<SPIDER_ROOT>`, `<BIRD_ROOT>`, `<gpu-alias>`, and endpoint URLs before running.

## Safety and Leakage Policy

- Gold SQL is used only by the evaluator.
- BIRD evidence and hidden annotations are not used in context construction, selection, rendering, or SQL generation.
- Stage A uses catalog metadata and read-only SQL profiling.
- Context stores and graphs are generated artifacts and are not bundled here because they can be rebuilt from official datasets.

## License

See `LICENSE_PLACEHOLDER.txt`. Replace it with the final license before public release. Third-party baselines must retain their original licenses.
