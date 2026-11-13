#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Python defaults to plan-only. Only an explicit --run starts fio.
exec python3 -B "${ROOT_DIR}/microbench/calibration_suite.py" "$@"
