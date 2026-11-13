#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# No --run means plan only. Additional options override the preset.
exec python3 -B "${ROOT_DIR}/microbench/calibration_suite.py" \
  --family arrival --paired-arrivals \
  --rates 2000 8000 16000 24000 32000 48000 64000 \
  --arrival-seconds 5 --repeats 4 --burst 8 --arrival-qd 128 "$@"
