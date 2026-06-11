# RQ1 Table 1 Reproduction Order and Progress

Generated: 2026-06-08 13:30:38

## Fixed Backbone

Gemma4-26B, because RQ1 Table 2 currently shows the best Spider performance among complete optimized-SDMC rows.

## Current Running Job

`sdmc_table1_core_gemma4` is running the core context rows first:

1. RAW_SCHEMA + Gemma4 on Spider dev, then BIRD dev.
2. HDC_STYLE / TiSQL-style LLM-generated context + Gemma4 on Spider dev, then BIRD dev.
3. SDMC + Gemma4 on Spider dev, then BIRD dev.

The local runner executes the three conditions interleaved by question. Full Spider core target is 1034 questions x 3 conditions = 3102 execution rows. Full BIRD core target is 1534 questions x 3 conditions = 4602 execution rows.

## Planned External Baseline Order

After core rows finish and the TiSQL reproduction smoke remains stable:

4. TiSQL full reproduction from `<WORKSPACE_ROOT>/TiInsight/repro/` with `--strategy tisql_full`.
5. CHESS adapter/reproduction.
6. DAIL-SQL adapter/reproduction.
7. DIN-SQL adapter/reproduction.
8. DeepEye-SQL adapter/reproduction.
9. MAC-SQL adapter/reproduction, unless we formally remove it from Table 1.

Each external baseline must use the same Gemma4 endpoint and the same local execution evaluator before being reported.

## Time Expectation

Current measured throughput fluctuates because Gemma4 is served through vLLM and GPU utilization is uneven. Core Spider+BIRD three-row run is expected to take several hours. TiSQL full is likely slower than SDMC core because it adds clarification and map-reduce LLM calls; expect roughly 2-4x per question unless we reduce calls with caching. Official-code baselines depend on adapter complexity and may take the rest of the day to smoke-test before full runs.
