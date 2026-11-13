#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:---plan}"
if [[ $# -gt 0 ]]; then shift; fi
common=(--family pipeline --batches 16 64 256 --rates 8192 16384 24576 32768
        --arrival-qd 512 --qds 16 64 256 512 --seed 20261114)
case "$MODE" in
  --plan) exec python3 -B "$ROOT_DIR/microbench/calibration_suite.py" "${common[@]}" --arrival-seconds 30 --runtime 30 --ramp 10 --repeats 3 "$@" ;;
  --pilot) exec python3 -B "$ROOT_DIR/microbench/calibration_suite.py" "${common[@]}" --arrival-seconds 2 --runtime 2 --ramp 0 --repeats 1 --run "$@" ;;
  --run) exec python3 -B "$ROOT_DIR/microbench/calibration_suite.py" "${common[@]}" --arrival-seconds 30 --runtime 30 --ramp 10 --repeats 3 --run "$@" ;;
  *) echo "Usage: bash $0 [--plan|--pilot|--run] [additional options]" >&2; exit 2 ;;
esac
