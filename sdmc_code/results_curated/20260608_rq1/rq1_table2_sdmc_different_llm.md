# RQ1 Table 2: SDMC with Different LLMs

Generated: 2026-06-08 13:30:38

Metric: Local Execution Match on copied local dev databases unless noted. These are complete rows only.

| Model | Dataset | N | EX (%) | Valid SQL (%) | Runtime Error (%) | Avg Gen Latency (s) |
|---|---:|---:|---:|---:|---:|---:|
| DeepSeek V4 Pro | BIRD dev | 1534 | 40.68 | 99.80 | 1.50 | 2.632 |
| Gemma4-26B | BIRD dev | 1534 | 38.46 | 100.00 | 5.08 | 1.732 |
| DeepSeek V4 Flash | BIRD dev | 1534 | 37.22 | 100.00 | 3.26 | 1.461 |
| Llama3-8B | BIRD dev | 1534 | 19.04 | 100.00 | 13.17 | 1.536 |
| Gemma4-26B | Spider dev | 1034 | 81.24 | 100.00 | 0.39 | 1.297 |
| DeepSeek V4 Pro | Spider dev | 1034 | 76.40 | 100.00 | 0.48 | 1.931 |
| DeepSeek V4 Flash | Spider dev | 1034 | 74.47 | 100.00 | 0.39 | 1.383 |
| Llama3-8B | Spider dev | 1034 | 61.80 | 100.00 | 2.32 | 1.265 |

## Non-mainline / Excluded Rows

- Qwen2.5-14B visible artifacts in `outputs/current_experiments/model_compare_sdmc_qwen25_14b_spider/` are invalid/empty-output and are not publishable as Table 2.
- Gemma3 artifacts exist from earlier runs, but they were produced under a different multi-condition/sample setup, so they are not mixed into this finalized optimized-SDMC Table 2.
- Current Table 2 winner for Spider-oriented Table 1 backbone: Gemma4-26B.
