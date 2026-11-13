#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mode="${1:---prepare}"
if [[ $# -gt 0 ]]; then shift; fi
case "$mode" in
  --prepare) exec python3 -B "$ROOT_DIR/microbench/mqsim_calibrate.py" prepare "$@" ;;
  --run)
    if [[ $# -lt 1 ]]; then echo 'Usage: bash microbench/run_mqsim_calibration.sh --run <prepared-directory>' >&2; exit 2; fi
    # Force rebuilding from current source; execution checks the prepared source hashes.
    make -C "$ROOT_DIR" -B -j "${BUILD_JOBS:-2}"
    exec python3 -B "$ROOT_DIR/microbench/mqsim_calibrate.py" run "$@" ;;
  --analyze) exec python3 -B "$ROOT_DIR/microbench/mqsim_calibrate.py" analyze "$@" ;;
  *) echo 'Modes: --prepare, --run <prepared-directory>, --analyze <completed-directory>' >&2; exit 2 ;;
esac
