#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
make -C "$PROJECT_ROOT" -B -j "${BUILD_JOBS:-2}"
exec python3 -B "$PROJECT_ROOT/microbench/validate_mqsim_observation.py"
