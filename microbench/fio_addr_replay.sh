#!/usr/bin/env bash
# Replay the read addresses of a DiskANN recording with fio at fixed queue depths, recording block-layer
# issue/complete events. Purpose: tell whether "every request slows down together" under DiskANN at high load
# comes from the SSD (it would also appear here, with almost no host compute) or from the host side.
# Read-only for the SSD apart from the small output files.
#
# Per run (<pattern>_qd<total>): block.dat (trace-cmd, CLOCK_MONOTONIC), fio.json, fio.job
# Sweep root: layout/ (filefrag before/after, dm table, partitions), iolog/, input.txt
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TRACE="${TRACE:-$ROOT_DIR/microbench/results/diskann_trace_20261115T121821Z_T48_W8_L100}"
INDEX="${INDEX:-/home/user/DiskANN/build/data/SIFT100M/disk_index_SIFT100M_base100M_R64_L100_A1.2_disk.index}"
DEV="${DEV:-nvme0n1}"
JOBS="${JOBS:-4}"
QDS=(${QDS:-128 192 256 400})
PATTERNS=(${PATTERNS:-diskann shuffled uniform})
OUT="${1:-$ROOT_DIR/microbench/results/fio_addr_replay_$(date -u +%Y%m%dT%H%M%SZ)}"

if [[ -e "$OUT" ]]; then echo "Refusing to overwrite existing $OUT" >&2; exit 1; fi
[[ -e "$INDEX" && -d "$TRACE" ]] || { echo "Missing $INDEX or $TRACE" >&2; exit 1; }
for qd in "${QDS[@]}"; do (( qd % JOBS == 0 )) || { echo "QD $qd is not a multiple of JOBS=$JOBS" >&2; exit 1; }; done
command -v fio >/dev/null && command -v trace-cmd >/dev/null && command -v filefrag >/dev/null
sudo -v
if [[ ! -s "$TRACE/mqsim_replay/real_requests.csv" ]]; then
    python3 "$ROOT_DIR/microbench/diskann_trace_to_mqsim.py" "$TRACE" > /dev/null
fi
mkdir -p "$OUT/layout"
OUT="$(realpath "$OUT")"
python3 "$ROOT_DIR/microbench/build_fio_iolog.py" "$TRACE" "$INDEX" "$OUT/iolog" --jobs "$JOBS"
cat > "$OUT/input.txt" <<EOF
source_trace=$TRACE
index=$INDEX
jobs=$JOBS qds=${QDS[*]} patterns=${PATTERNS[*]}
device=$DEV scheduler=$(cat /sys/block/$DEV/queue/scheduler) nr_requests=$(cat /sys/block/$DEV/queue/nr_requests)
fio=$(fio --version) kernel=$(uname -r)
EOF
cat "$OUT/input.txt"
filefrag -v -b512 "$INDEX" > "$OUT/layout/disk_index.extents.before.txt"
findmnt -T "$INDEX" -o SOURCE,FSTYPE,TARGET > "$OUT/layout/mount.txt"
lsblk -o NAME,MAJ:MIN,SIZE,TYPE,PKNAME,START > "$OUT/layout/lsblk.txt"
sudo dmsetup table > "$OUT/layout/dmsetup_table.txt"
for p in /sys/block/$DEV/${DEV}p*; do echo "$(basename "$p") start=$(cat "$p/start") size=$(cat "$p/size")"; done > "$OUT/layout/partitions.txt"
cat /sys/block/$DEV/dev > "$OUT/layout/dev_majmin.txt"

for pattern in "${PATTERNS[@]}"; do
    for qd in "${QDS[@]}"; do
        run="$OUT/${pattern}_qd${qd}"; mkdir "$run"
        {
            echo "[global]"
            echo "ioengine=libaio"; echo "direct=1"; echo "thread=1"; echo "group_reporting=1"
            echo "iodepth=$((qd / JOBS))"
            echo "iodepth_batch_submit=1"; echo "iodepth_batch_complete_min=1"; echo "iodepth_batch_complete_max=1"
            for ((j = 0; j < JOBS; j++)); do echo; echo "[j$j]"; echo "read_iolog=$OUT/iolog/${pattern}_job$j.iolog"; done
        } > "$run/fio.job"
        echo "=== $pattern QD$qd ==="
        sudo trace-cmd record -C mono -b 65536 -o "$run/block.dat" \
            -e block:block_rq_issue -e block:block_rq_complete \
            sudo -u "$(id -un)" fio --readonly --output-format=json --output="$run/fio.json" "$run/fio.job" \
            > "$run/record.log" 2>&1
        sudo chown -R "$(id -un)" "$run"
        python3 - "$run/fio.json" <<'EOF'
import json, sys
j = json.load(open(sys.argv[1]))['jobs'][0]['read']
print(f"  ios={j['total_ios']} iops={j['iops']:.0f} clat_mean_us={j['clat_ns']['mean'] / 1000:.1f} "
      f"p50={j['clat_ns']['percentile']['50.000000'] / 1000:.1f} p99={j['clat_ns']['percentile']['99.000000'] / 1000:.1f}")
EOF
        sleep 3
    done
done
filefrag -v -b512 "$INDEX" > "$OUT/layout/disk_index.extents.after.txt"
if cmp -s "$OUT/layout/disk_index.extents.before.txt" "$OUT/layout/disk_index.extents.after.txt"; then
    echo "Index extents unchanged during the sweep."
else
    echo "WARNING: index extents changed during the sweep." >&2
fi
rm -rf "$OUT/iolog"   # large and reproducible from the source trace (seeded)
du -sh "$OUT"
echo "Done: $OUT"
