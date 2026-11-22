#!/usr/bin/env bash
# Open-loop replay of a converted DiskANN block trace (trace_replay/diskann_trace_to_mqsim.py output) in MQSim,
# then compare per-request latency against the real device.
# Usage: bash microbench/trace_replay/run_mqsim_trace_replay.sh <replay_dir> [ssdconfig.xml]
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPLAY="$(realpath "${1:?usage: run_mqsim_trace_replay.sh <replay_dir> [ssdconfig.xml]}")"
CONFIG="$(realpath "${2:-$ROOT_DIR/microbench/ssdconfig_t500_calibrated.xml}")"
RUN="$REPLAY/run_$(basename "$CONFIG" .xml)"
[[ -s "$REPLAY/mqsim_trace.txt" ]] || { echo "Missing $REPLAY/mqsim_trace.txt" >&2; exit 1; }
if [[ -e "$RUN" ]]; then echo "Refusing to overwrite existing $RUN" >&2; exit 1; fi
make -C "$ROOT_DIR" -j "${BUILD_JOBS:-4}"
mkdir -p "$RUN"
cp "$CONFIG" "$RUN/ssdconfig.xml"
cp "$ROOT_DIR/MQSim" "$RUN/MQSim.binary"
python3 - "$RUN/ssdconfig.xml" "$REPLAY/mqsim_trace.txt" "$RUN/workload.xml" <<'EOF'
import sys, xml.etree.ElementTree as ET
cfg, trace, out = sys.argv[1:]
c = ET.parse(cfg)
ids = lambda tag: ','.join(map(str, range(int(c.findtext('.//' + tag)))))
root = ET.Element('MQSim_IO_Scenarios'); sc = ET.SubElement(root, 'IO_Scenario')
f = ET.SubElement(sc, 'IO_Flow_Parameter_Set_Trace_Based')
for k, v in [('Priority_Class', 'HIGH'), ('Device_Level_Data_Caching_Mode', 'WRITE_CACHE'),
             ('Channel_IDs', ids('Flash_Channel_Count')), ('Chip_IDs', ids('Chip_No_Per_Channel')),
             ('Die_IDs', ids('Die_No_Per_Chip')), ('Plane_IDs', ids('Plane_No_Per_Die')),
             ('Initial_Occupancy_Percentage', 75), ('File_Path', trace), ('Percentage_To_Be_Executed', 100),
             ('Relay_Count', 1), ('Time_Unit', 'NANOSECOND')]:
    ET.SubElement(f, k).text = str(v)
ET.ElementTree(root).write(out, encoding='us-ascii', xml_declaration=True)
EOF
echo "Replaying $(wc -l < "$REPLAY/mqsim_trace.txt") requests with $CONFIG"
( cd "$RUN" && MQSIM_REQUEST_LOG="$RUN/mqsim_requests.txt" /usr/bin/time -v "$RUN/MQSim.binary" -i ssdconfig.xml -w workload.xml \
    < /dev/null > stdout.txt 2> stderr.txt )
grep -h "FTL_EXIT\|HOST_EXIT" "$RUN/stderr.txt" || true
python3 "$ROOT_DIR/microbench/trace_replay/compare_trace_replay.py" "$REPLAY" "$RUN"
