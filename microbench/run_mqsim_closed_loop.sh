#!/usr/bin/env bash
# Closed-loop (completion-dependent) MQSim replay of a DiskANN recording, then compare with the real run.
# Usage: bash microbench/run_mqsim_closed_loop.sh <trace_dir> [ssdconfig.xml]
#   ROUNDS_PER_WORKER=N  replay only each worker's first N rounds (smoke test); default all rounds
#   TAG=name             output directory suffix (default: config name, plus _smokeN)
# Needs <trace_dir>/mqsim_replay (diskann_trace_to_mqsim.py) and <trace_dir>/closed_loop (build_closed_loop_schedule.py).
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TRACE="$(realpath "${1:?usage: run_mqsim_closed_loop.sh <trace_dir> [ssdconfig.xml]}")"
CONFIG="$(realpath "${2:-$ROOT_DIR/microbench/ssdconfig_t500_calibrated.xml}")"
ROUNDS="${ROUNDS_PER_WORKER:-0}"
if [[ -z "${TAG:-}" ]]; then
    TAG="$(basename "$CONFIG" .xml)"
    if [[ "$ROUNDS" != 0 ]]; then TAG="${TAG}_smoke${ROUNDS}"; fi
fi
RUN="$TRACE/closed_loop/run_$TAG"
for f in "$TRACE/mqsim_replay/mqsim_trace.txt" "$TRACE/closed_loop/schedule.txt" "$TRACE/closed_loop/real_rounds.csv"; do
    [[ -s "$f" ]] || { echo "Missing $f" >&2; exit 1; }
done
if [[ -e "$RUN" ]]; then echo "Refusing to overwrite existing $RUN" >&2; exit 1; fi
make -C "$ROOT_DIR" -j "${BUILD_JOBS:-4}"
mkdir -p "$RUN"
cp "$CONFIG" "$RUN/ssdconfig.xml"
cp "$ROOT_DIR/MQSim" "$RUN/MQSim.binary"
if [[ "$ROUNDS" != 0 ]]; then
    awk -v k="$ROUNDS" '/^#/ {print; next} { if (++c[$1] <= k) print }' "$TRACE/closed_loop/schedule.txt" > "$RUN/schedule.txt"
else
    ln -s "$TRACE/closed_loop/schedule.txt" "$RUN/schedule.txt"
fi
python3 - "$RUN/ssdconfig.xml" "$TRACE/mqsim_replay/mqsim_trace.txt" "$RUN/schedule.txt" "$RUN/workload.xml" <<'EOF'
import sys, xml.etree.ElementTree as ET
cfg, trace, schedule, out = sys.argv[1:]
c = ET.parse(cfg)
ids = lambda tag: ','.join(map(str, range(int(c.findtext('.//' + tag)))))
root = ET.Element('MQSim_IO_Scenarios'); sc = ET.SubElement(root, 'IO_Scenario')
f = ET.SubElement(sc, 'IO_Flow_Parameter_Set_Trace_Based')
for k, v in [('Priority_Class', 'HIGH'), ('Device_Level_Data_Caching_Mode', 'WRITE_CACHE'),
             ('Channel_IDs', ids('Flash_Channel_Count')), ('Chip_IDs', ids('Chip_No_Per_Channel')),
             ('Die_IDs', ids('Die_No_Per_Chip')), ('Plane_IDs', ids('Plane_No_Per_Die')),
             ('Initial_Occupancy_Percentage', 75), ('File_Path', trace), ('Percentage_To_Be_Executed', 100),
             ('Relay_Count', 1), ('Time_Unit', 'NANOSECOND'), ('Replay_Schedule_File', schedule)]:
    ET.SubElement(f, k).text = str(v)
ET.ElementTree(root).write(out, encoding='us-ascii', xml_declaration=True)
EOF
echo "Closed-loop replay ($( [[ "$ROUNDS" != 0 ]] && echo "first $ROUNDS rounds per worker" || echo "all rounds")) with $CONFIG"
if ( cd "$RUN" && MQSIM_REQUEST_LOG="$RUN/mqsim_requests.txt" MQSIM_ROUND_LOG="$RUN/mqsim_rounds.txt" \
    MQSIM_TRANSACTION_LOG="$RUN/mqsim_transactions.txt" \
    /usr/bin/time -v "$RUN/MQSim.binary" -i ssdconfig.xml -w workload.xml < /dev/null > stdout.txt 2> stderr.txt ); then
    :
else
    replay_status=$?
    printf 'MQSim failed (status %s). Output: %s\n' "$replay_status" "$RUN" >&2
    printf '%s\n' "$replay_status" > "$RUN/FAILED"
    tail -n 35 "$RUN/stderr.txt" >&2
    exit "$replay_status"
fi
grep -h "FTL_EXIT\|HOST_EXIT\|Closed-loop replay" "$RUN/stderr.txt" "$RUN/stdout.txt" || true
python3 "$ROOT_DIR/microbench/compare_closed_loop.py" "$TRACE" "$RUN"
python3 "$ROOT_DIR/microbench/analyze_ssd_slack.py" "$RUN" > /dev/null && echo "SSD slack: $RUN/slack.json"
