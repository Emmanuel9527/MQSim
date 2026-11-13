#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${1:-}" != '--analyze' ]]; then
  make -C "$PROJECT_ROOT" -B -j "${BUILD_JOBS:-2}"
fi
exec python3 -B "$PROJECT_ROOT/microbench/mqsim_window_probe.py" "$@"
