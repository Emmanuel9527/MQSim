#!/usr/bin/env bash
# Idle-gap recovery probe: does a short idle gap put the SSD back into its fast state,
# and does that depend on APST (autonomous power-state transitions)?
# Read-only for the SSD (fio --readonly). APST is toggled through the kernel's PM QoS knob
# and restored to its original value on exit.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-${ROOT_DIR}/microbench/results/gap_probe_20261114}"
DEV="${DEV:-nvme0}"
FILE="${INPUT_FILE:-/home/emmanuel/projects/data/sift100m_diskann_indexes/sift100m_l2_R64_L100_pq0_block_shuffle_disk.index}"
QOS="/sys/class/nvme/${DEV}/power/pm_qos_latency_tolerance_us"
GAPS_MS=(${GAPS_MS:-0 20 50 100 200 500 1000 3000})
QD="${QD:-16}"
BURST="${BURST:-2s}"
RUN_S="${RUN_S:-40}"
if [[ -e "$OUT" ]]; then echo "Refusing to overwrite existing $OUT" >&2; exit 1; fi
mkdir -p "$OUT"
ORIG_QOS="$(cat "$QOS")"
echo "$ORIG_QOS" > "$OUT/orig_pm_qos_latency_tolerance_us.txt"
sudo -v
if [[ -b "$FILE" ]]; then FILE_BYTES=$(sudo blockdev --getsize64 "$FILE"); else FILE_BYTES=$(stat -c %s "$FILE"); fi
SIZE_BYTES="${SIZE_BYTES:-$(( FILE_BYTES < 34359738368 ? FILE_BYTES : 34359738368 ))}"
echo "input=$FILE file_bytes=$FILE_BYTES read_span_bytes=$SIZE_BYTES" | tee "$OUT/input.txt"
trap 'echo "$ORIG_QOS" | sudo tee "$QOS" >/dev/null; echo "restored $QOS=$(cat "$QOS")"' EXIT

write_job() {  # $1 = mode tag
    local job="$OUT/$1.fio"
    cat > "$job" <<EOF
[global]
ioengine=libaio
direct=1
rw=randread
bs=4096
filename=${FILE}
allow_file_create=0
size=${SIZE_BYTES}
offset=0
iodepth=${QD}
iodepth_batch_submit=1
iodepth_batch_complete_min=1
iodepth_batch_complete_max=1
norandommap=1
randrepeat=1
randseed=20261114
invalidate=0
thread=1
time_based=1
log_avg_msec=100
log_unix_epoch=1
stonewall

[${1}_warm]
runtime=30
write_iops_log=${OUT}/${1}_warm
EOF
    for g in "${GAPS_MS[@]}"; do
        {
            echo
            echo "[${1}_gap${g}]"
            echo "runtime=${RUN_S}"
            if [[ "$g" != 0 ]]; then
                echo "thinktime=${g}ms"
                echo "thinktime_iotime=${BURST}"
            fi
            echo "write_iops_log=${OUT}/${1}_gap${g}"
        } >> "$job"
    done
    echo "$job"
}

run_mode() {  # $1 = tag, $2 = pm_qos value
    echo "$2" | sudo tee "$QOS" >/dev/null
    sleep 1
    sudo nvme get-feature "/dev/${DEV}" -f 0x0c -H > "$OUT/$1_apst.txt" 2>&1 || true
    echo "[$1] pm_qos=$2, APST: $(head -2 "$OUT/$1_apst.txt" | tail -1 | tr -s ' \t' ' ')"
    fio --readonly --output-format=json --output="$OUT/$1.json" "$(write_job "$1")"
}

run_mode apst_on "$ORIG_QOS"
run_mode apst_off 0
echo "Done: $OUT"
