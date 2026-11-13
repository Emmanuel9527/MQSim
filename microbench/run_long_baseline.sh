#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:---run}"
if [[ $# -gt 1 || ( "$MODE" != --run && "$MODE" != --check ) ]]; then
    echo "Usage: bash $0 [--run|--check]" >&2
    exit 1
fi
read -r -a QD_LIST <<< "${QDS:-1 2 4 8 16 32 64 128}"
RUN_NAME="mqsim_long_$(date -u +%Y%m%dT%H%M%SZ)_$(python3 -c 'import uuid; print(uuid.uuid4().hex[:8])')"
RAM_ROOT="${RAM_ROOT:-/dev/shm}"
ARCHIVE_ROOT="${ARCHIVE_ROOT:-${ROOT_DIR}/microbench/results}"
args=(--qds "${QD_LIST[@]}" --runtime "${RUNTIME:-300}" --ramp "${RAMP:-30}"
      --repeats "${REPEATS:-3}" --seed "${SEED:-20261112}"
      --size-gib "${SIZE_GIB:-32}" --block-kib 4
      --out "${RAM_ROOT}/${RUN_NAME}" --archive-root "$ARCHIVE_ROOT")
if [[ -n "${INPUT_FILE:-}" ]]; then args+=(--file "$INPUT_FILE"); fi
if [[ -n "${CPU:-}" ]]; then args+=(--cpu "$CPU"); fi
echo "4KiB random READ, one libaio job; no writes to the input file."
echo "Final results: ${ARCHIVE_ROOT}/${RUN_NAME}"
echo "QD sweep estimates closed-loop behavior, not fixed-rate burst response."
if [[ "$MODE" == --check ]]; then
    python3 "${ROOT_DIR}/microbench/read_sweep.py" "${args[@]}"
    exit 0
fi
python3 -c 'import matplotlib'  # Check plotting dependency before a long run.
python3 "${ROOT_DIR}/microbench/read_sweep.py" "${args[@]}" --run
python3 "${ROOT_DIR}/microbench/analyze_read_sweep.py" "${ARCHIVE_ROOT}/${RUN_NAME}"
python3 "${ROOT_DIR}/microbench/check_convergence.py" "${ARCHIVE_ROOT}/${RUN_NAME}"
echo "Done: ${ARCHIVE_ROOT}/${RUN_NAME}/analysis"
