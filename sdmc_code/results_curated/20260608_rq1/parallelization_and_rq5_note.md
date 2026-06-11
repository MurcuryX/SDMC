# RQ Experiment Parallelization Note

Generated: 2026-06-08 13:39 CST

## What Can Be Parallelized Safely Now

Safe immediately:

- CPU-side result curation: difficulty aggregation, RQ5 latency/token aggregation, report table generation.
- External baseline adapter development and smoke preparation.
- Reading official baseline repositories / TiInsight code.

Not safe to start blindly:

- A second full run writing to the same `rq1_table1_core_gemma4_bird` output directory. The existing `sdmc_table1_core_gemma4` tmux command will start BIRD after Spider; if another BIRD process is still running then, the two processes can duplicate rows.
- A second full Gemma4 baseline on the same endpoint while the core Table1 run is active. vLLM can batch concurrent requests, but latency/error behavior changes and it may slow the core run. Use only after a small throughput test or on a separate Gemma4 endpoint.

## Best Efficiency Option

GPU2 currently holds an idle Llama3 vLLM service. If we stop that service and start a second Gemma4 endpoint on GPU2, then we can safely parallelize by assigning disjoint baselines to separate endpoints/output dirs:

- GPU3 Gemma4 endpoint: continue current RAW_SCHEMA / HDC_STYLE / SDMC core run.
- GPU2 Gemma4 endpoint: TiSQL full or one external baseline smoke/full run.

This is the cleanest way to improve wall-clock time without corrupting outputs, but stopping the Llama3 service is a process-kill operation and should be explicitly approved.

## Spider Difficulty and RQ5

No remeasurement is needed for Spider easy/medium/hard/extra/all. The run artifacts can be post-processed using the official Spider evaluator hardness logic from TiInsight's bundled `test-suite-sql-eval`.

New curation script:

```bash
python3 scripts/curate_rq_metrics.py \
  --run-dir <run_dir> \
  --dataset spider \
  --root outputs/rq_final_20260608_023504/local_data/roots/spider \
  --out results_curated/20260608_rq1/<name>_by_difficulty_rq5.csv
```

The output includes:

- EX by difficulty and all.
- valid SQL rate.
- runtime error rate.
- average generation latency.
- average execution latency.
- prompt/completion token statistics.
- repair attempts.
