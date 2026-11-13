#!/usr/bin/env bash
# Device-state probe: correlates per-second fio IOPS with NVMe temperature,
# host-side traffic on the same disk (/proc/diskstats), and SMART thermal counters.
# Read-only for the SSD: fio uses --readonly on the existing index file; nvme commands are queries.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-${ROOT_DIR}/microbench/results/state_probe_20261114}"
DEV="${DEV:-nvme0}"
NS="${NS:-nvme0n1}"
FILE="${INPUT_FILE:-/home/emmanuel/projects/data/sift100m_diskann_indexes/sift100m_l2_R64_L100_pq0_block_shuffle_disk.index}"
HWMON="$(grep -l '^nvme$' /sys/class/hwmon/hwmon*/name | head -1 | xargs dirname)"
IDLE_S="${IDLE_S:-120}"
if [[ -e "$OUT" ]]; then echo "Refusing to overwrite existing $OUT" >&2; exit 1; fi
mkdir -p "$OUT"
sudo -v  # nvme smart-log/get-feature need root; ask once up front.

snapshot() {  # $1 = tag
    sudo nvme smart-log "/dev/${DEV}" -o json > "$OUT/smart_$1.json"
    sudo nvme get-feature "/dev/${DEV}" -f 0x02 -H > "$OUT/power_$1.txt" 2>&1 || true
}
sudo nvme id-ctrl "/dev/${DEV}" -H > "$OUT/id_ctrl.txt"
sudo nvme get-feature "/dev/${DEV}" -f 0x0d -H > "$OUT/hmb.txt" 2>&1 || true
sudo nvme get-feature "/dev/${DEV}" -f 0x0c -H > "$OUT/apst.txt" 2>&1 || true
sudo nvme get-feature "/dev/${DEV}" -f 0x10 -H > "$OUT/hctm.txt" 2>&1 || true
cat /sys/module/nvme/parameters/max_host_mem_size_mb > "$OUT/max_host_mem_size_mb.txt" 2>/dev/null || true

# 1 Hz sampler: time_ns, phase, temps (milli-degC), diskstats fields for the namespace.
echo "time_ns,phase,temp1,temp2,temp3,reads_done,sectors_read,writes_done,sectors_written,io_ticks_ms" > "$OUT/samples.csv"
echo idle0 > "$OUT/.phase"
(
    while [[ -e "$OUT/.phase" ]]; do
        ph="$(cat "$OUT/.phase" 2>/dev/null || echo end)"
        t1=$(cat "$HWMON/temp1_input"); t2=$(cat "$HWMON/temp2_input" 2>/dev/null || echo); t3=$(cat "$HWMON/temp3_input" 2>/dev/null || echo)
        ds=$(awk -v d="$NS" '$3==d {print $4","$6","$8","$10","$13}' /proc/diskstats)
        echo "$(date +%s%N),$ph,$t1,$t2,$t3,$ds" >> "$OUT/samples.csv"
        sleep 1
    done
) &
SAMPLER=$!
trap 'rm -f "$OUT/.phase"; wait $SAMPLER 2>/dev/null || true' EXIT

run_fio() {  # $1 = tag, $2 = iodepth, $3 = runtime seconds
    echo "$1" > "$OUT/.phase"
    fio --readonly --output-format=json --output="$OUT/fio_$1.json" \
        --name="$1" --ioengine=libaio --direct=1 --rw=randread --bs=4096 \
        --filename="$FILE" --allow_file_create=0 --size=34359738368 --offset=0 \
        --iodepth="$2" --iodepth_batch_submit=1 --iodepth_batch_complete_min=1 --iodepth_batch_complete_max=1 \
        --time_based=1 --runtime="$3" --ramp_time=0 --norandommap=1 --randrepeat=1 --randseed=20261114 \
        --invalidate=0 --thread=1 --numjobs=1 \
        --write_iops_log="$OUT/iops_$1" --log_avg_msec=1000 --log_unix_epoch=1
    snapshot "after_$1"
}
idle() {  # $1 = tag
    echo "$1" > "$OUT/.phase"; sleep "$IDLE_S"; snapshot "after_$1"
}

snapshot start
echo "[1/6] idle ${IDLE_S}s (background host traffic + temperature)"; idle idle0
echo "[2/6] QD16 randread 300s";                                    run_fio qd16_a 16 300
echo "[3/6] idle ${IDLE_S}s";                                        idle idle1
echo "[4/6] QD1 randread 180s";                                      run_fio qd1 1 180
echo "[5/6] QD128 randread 180s";                                    run_fio qd128 128 180
echo "[6/6] idle ${IDLE_S}s then QD16 randread 120s";                idle idle2; run_fio qd16_b 16 120
echo end > "$OUT/.phase"; sleep 2
echo "Done: $OUT"
