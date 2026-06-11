# SDMC Current Experiment Tables

Updated: 2026-06-09 18:37 CST

Note: `---` means there is currently no reliable full-result value on disk, or the item is not yet finished; running / sampled / pilot results are not passed off as final results.

## RQ1 Table 1: Overall Performance

| Method | Spider Easy | Spider Medium | Spider Hard | Spider Extra | Spider All | BIRD Dev EX | BIRD Test EX |
|---|---:|---:|---:|---:|---:|---:|---:|
| Raw Schema | --- | --- | --- | --- | 72.53 | 28.23 | --- |
| TiSQL / TiInsight-style | --- | --- | --- | --- | 76.79 | 53.46 | --- |
| SDMC (Ours) | 94.35 | 80.04 | 81.03 | 65.06 | 81.24 | 38.46 | --- |
| MAC-SQL | --- | --- | --- | --- | 81.43 | 21.19 | --- |
| DAIL-SQL | --- | --- | --- | --- | 76.60 | 53.65 | --- |
| DIN-SQL | --- | --- | --- | --- | 79.88 | --- | --- |
| CHESS | --- | --- | --- | --- | --- | --- | --- |
| DeepEye-SQL | --- | --- | --- | --- | --- | --- | --- |

This table measures the overall execution accuracy of different Text-to-SQL methods on Spider and BIRD under the same LLM setting.
It is run to answer whether SDMC is competitive in primary performance against raw schema, TiInsight-style, and mainstream Text-to-SQL frameworks.
On Spider, SDMC/Gemma4 reaches 81.24, close to MAC-SQL but short of the 86+ target; on BIRD, SDMC clearly trails TiSQL/DAIL, so the context selection and SQL generation strategy on complex databases needs further analysis.

## RQ1 Table 2: SDMC with Different LLMs

| LLM | Spider Dev EX | BIRD Dev EX | Avg Gen Latency Spider (s) | Avg Gen Latency BIRD (s) | Status |
|---|---:|---:|---:|---:|---|
| Gemma4-26B | 81.24 | 38.46 | 1.297 | 1.732 | complete |
| DeepSeek V4 Pro | 76.40 | 40.68 | 1.931 | 2.632 | complete |
| DeepSeek V4 Flash | 74.47 | 37.22 | 1.383 | 1.461 | complete |
| Llama3-8B | 61.80 | 19.04 | 1.265 | 1.536 | complete |
| Qwen2.5-14B | --- | --- | --- | --- | needs final full table entry |
| Gemma3 | --- | --- | --- | --- | not selected; earlier weaker than Gemma4 |

This table measures how different LLM backends affect the final Text-to-SQL EX once the SDMC framework is fixed.
It is run first to pick the main model used consistently across Table 1/RQ2/RQ3/RQ4, so that method differences are not confounded with model differences.
Currently Gemma4 is the best-fitting model on Spider and DeepSeek V4 Pro is stronger on BIRD; per our earlier decision, Table 1 uses Gemma4 consistently, but DeepSeek Pro's advantage on BIRD still needs attention.

## RQ2 Table 3: Context Store and Context Graph

| Variant | Spider Dev EX | BIRD Dev EX |
|---|---:|---:|
| RAW_SCHEMA | 72.53 | 28.23 |
| SDMC (Store + Graph) | 80.95 | 38.53 |
| SDMC_FLAT_STORE | 77.56 | 30.05 |
| SDMC_FULL | 79.01 | 30.31 |
| SDMC_GRAPH_NO_REL | 79.69 | 36.44 |
| SDMC_GRAPH_SCHEMA_ONLY | 74.95 | 29.14 |

This table measures the separate contributions of the Context Store, Context Graph, relation edges, and selection strategy to SDMC.
It is run to show that SDMC's novelty is not just "stuffing in more context", but that the combination of SQL-derived facts + graph-aware selection is genuinely effective.
The results largely meet expectations: the full Store+Graph significantly improves over raw schema, the graph relation contribution is especially clear on BIRD, but the drop of `SDMC_FULL` on BIRD shows that full stuffing introduces noise.

## RQ3 Table 4: Different Context Generation for Text-to-SQL

| Context Generation | Method | Spider Dev EX | BIRD Dev EX |
|---|---|---:|---:|
| Schema-based | MAC-SQL | 81.91 | 60.95 |
| LLM-based | MAC-SQL | 82.40 | --- |
| SQL-based | MAC-SQL | 82.79 | --- |
| Schema-based | TiSQL | 79.98 | --- |
| LLM-based | TiSQL | 78.05 | --- |
| SQL-based | TiSQL | 79.98 | --- |
| Schema-based | SDMC | 72.44 | 28.23 |
| LLM-based | SDMC | 81.33 | 39.70 |
| SQL-based | SDMC | 81.04 | 38.14 |

This table measures the performance of three context sources — schema-based, LLM-based HDC, and SQL-based SDMC context — when fed to the three methods MAC-SQL/TiSQL/SDMC.
It is run to separate the contribution of the "context generation method" from that of the "downstream Text-to-SQL framework", supporting the interpretability and transferability of SQL-derived context.
Spider already shows that SQL-based context gives a positive gain for MAC-SQL, but only part of the BIRD rows are finished; TiSQL BIRD-LLM is currently stuck, so unfinished results must not be written into the conclusions.

## RQ4 Table 5: Three-Level Context Ablation

| Variant | Spider Dev EX | BIRD Dev EX |
|---|---:|---:|
| SDMC full | 81.24 | 38.40 |
| w/o Column Context | 76.31 | 31.03 |
| w/o Table Context | 81.04 | 37.74 |
| w/o Database Context | 80.95 | 38.33 |
| only Column Context | 80.75 | 38.46 |
| only Table Context | 76.60 | 30.77 |
| only Database Context | 75.82 | 31.16 |

This table measures the independent contribution of the three-level (column/table/database) SQL-derived context and the impact of removing each.
It is run to answer whether all three context levels are necessary, and which levels are the main source of SDMC's performance.
The results show column context is the most critical, while table/database context act more like supplementary constraints; on BIRD, only-column is even slightly above full, indicating that on complex databases table/database context can still introduce selection noise, which needs careful explanation in the paper.

## RQ5 Table 6: Latency and Cost

| Setting | Spider Avg Gen Latency (s) | BIRD Avg Gen Latency (s) | API Cost | GPU Cost / Notes |
|---|---:|---:|---:|---|
| SDMC + Gemma4-26B | 1.297 | 1.732 | 0 | local GPU; full GPU-hour accounting pending |
| SDMC + DeepSeek V4 Pro | 1.931 | 2.632 | --- | API token-cost summary pending |
| SDMC + DeepSeek V4 Flash | 1.383 | 1.461 | --- | API token-cost summary pending |
| SDMC + Llama3-8B | 1.265 | 1.536 | 0 | local GPU; weak accuracy |
| RQ2/RQ4 SDMC variants | --- | --- | 0 | latency available in aggregate CSV, final figure pending |

This table measures generation latency, API cost, and local GPU cost under different models and SDMC settings.
It is run to show that SDMC does not only chase EX, but also meets ICDE's requirements on efficiency, deployability, and cost.
So far only the generation latency of the main models has been compiled; the cost statistics and the unified latency figure for RQ2/RQ4 are not finalized yet, so RQ5 is not fully complete.

## Current GPU Status

| GPU | Current Status | Interpretation |
|---|---|---|
| GPU1 / port 18115 | Gemma4 endpoint loaded, util 0%; TiSQL BIRD-LLM stuck at 371/1534 | abnormal; not model loading |
| GPU3 / port 18114 | Gemma4 endpoint loaded, util 100%; MAC-SQL BIRD-LLM running | normal |

GPU3 is being used by MAC-SQL's BIRD-LLM condition, not idling.
GPU1's TiSQL process is still running on the CPU but the predicted row count is not growing, indicating it is stuck on a single question, in the SQL/refine/eval or I/O stage, rather than loading normally.
Going forward, add a per-question timeout to TiSQL BIRD-LLM or restart with checkpoint resume, to avoid the endpoint holding GPU memory for a long time without any requests.
