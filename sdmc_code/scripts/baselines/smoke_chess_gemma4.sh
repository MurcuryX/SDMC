#!/bin/bash
set -euo pipefail

ROOT="<SDMC_ROOT>"
if [ "${SDMC_GEMMA4_LOCKED:-0}" != "1" ]; then
  export SDMC_GEMMA4_LOCKED=1
  exec "$ROOT/scripts/baselines/with_gemma4_endpoint_lock.sh" "$0" "$@"
fi
cd "$ROOT/external_baselines/CHESS"

set -a
. "$PWD/.env.sdmc_smoke"
set +a

export PYTHONPATH="$ROOT/.baseline_envs/chess_minimal:$PWD/src"
export TOKENIZERS_PARALLELISM=false

bash run/run_main_sdmc_smoke.sh
