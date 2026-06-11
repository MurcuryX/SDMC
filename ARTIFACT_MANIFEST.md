# Artifact Manifest

Generated for ICDE open-source submission.

## Core Code

- `sdmc/src/sdmc/stage_a.py`: offline SQL-derived context construction.
- `sdmc/src/sdmc/store.py`: SQLite-backed Context Store.
- `sdmc/src/sdmc/graph.py`: Context Graph materialization.
- `sdmc/src/sdmc/stage_b.py`: question-time selector, renderer, model adapter, SQL extraction, and evaluator.
- `sdmc/src/sdmc/experiment.py`: experiment runner for RAW_SCHEMA, HDC_STYLE, and SDMC conditions.
- `sdmc/src/sdmc/reports.py`: aggregate reporting utilities.
- `sdmc/src/sdmc/cli.py`: command-line interface.

## Experiment Code

- `sdmc/scripts/run_stage_a_full_v2.sh`: full Stage A build wrapper.
- `sdmc/scripts/run_rq2_gemma4*.sh`: RQ2 Store/Graph ablation.
- `sdmc/scripts/run_rq4_gemma4*.sh`: RQ4 multi-level context ablation.
- `sdmc/scripts/run_sensitivity_singlevar_server1.sh`: budget sensitivity.
- `sdmc/scripts/benchmark_store_graph_cost.py`: Context Store/Graph cost benchmark.
- `sdmc/scripts/benchmark_rq3_context_generation_speed.py`: context generation speed benchmark.
- `sdmc/scripts/baselines/`: baseline adapter wrappers.

## Documentation

- `docs/stage_a_full_dataset_context_construction.md`
- `docs/stage_b_question_time_selected_sdmc.md`
- `docs/SOP_RQ1_baseline_and_model_selection.md`
- `docs/SOP_RQ2_context_store_graph.md`
- `docs/SOP_RQ3_different_context_selection.md`
- `docs/SOP_RQ4_RQ5_ablation_cost_latency.md`
- `docs/SDMC_EXPERIMENT_REFERENCE_FOR_PAPER.md`

## Excluded From This Artifact

- API keys and private credentials.
- Raw Spider/BIRD datasets.
- Generated `context_store.sqlite` files.
- Full raw outputs, logs, checkpoints, model weights.
- Virtual environments and dependency caches.
- Third-party baseline repositories unless explicitly included by their own license.

## Reproducibility Notes

Use official Spider/BIRD datasets and configure paths locally. For local LLM experiments, serve the selected model through an OpenAI-compatible endpoint and point the config files at that endpoint. For API experiments, keep credentials outside this repository.
