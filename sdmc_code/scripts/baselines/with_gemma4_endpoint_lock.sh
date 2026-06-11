#!/bin/bash
set -euo pipefail

LOCK_FILE="${GEMMA4_ENDPOINT_LOCK:-/tmp/sdmc_gemma4_endpoint_18114.lock}"
shifted=("$@")
if [ "${#shifted[@]}" -eq 0 ]; then
  echo "Usage: $0 <command...>" >&2
  exit 2
fi

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "Gemma4 endpoint lock is held: $LOCK_FILE" >&2
  echo "Run one full/smoke job per endpoint, or start a separate endpoint/port." >&2
  exit 4
fi

exec "${shifted[@]}"
