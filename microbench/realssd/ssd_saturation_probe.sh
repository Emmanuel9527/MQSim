#!/usr/bin/env bash
# Where does an SSD saturate first: NAND, or the controller's per-command path? Read-only fio probes on an existing
# large file (O_DIRECT, no writes). Each run: fio.json in <OUT>/<name>/, summary in <OUT>/summary.csv.
#   E1  4 KiB random read, total queue depth sweep            -> saturation IOPS, latency knee
#   E2  random read size sweep (4K..64K) at QD 32 and 128      -> IOPS flat with size = per-command bound;
#                                                                  bandwidth flat = channel/PCIe bound
#   E3  = E1 at QD 1                                           -> tR + fixed overhead
#   E4  spare-capacity probe: 4K random QD128 alone, 128K sequential QD16 alone, both together
#                                                               -> random IOPS kept while sequential adds GB/s
#                                                                  means NAND had spare capacity
#   E5  QD1 4K random read over growing address ranges (StrideANN Fig. 5: data/L2P caching)
#   E6  16 outstanding 4K random reads confined to ranges 2M..full, 3 region offsets (StrideANN Fig. 9)
# Usage: realssd/ssd_saturation_probe.sh [OUT]   (env: FILE, RUNTIME, RAMP, ENGINE)
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FILE="${FILE:-/home/user/DiskANN/build/data/SIFT100M/disk_index_SIFT100M_base100M_R64_L100_A1.2_disk.index}"
RUNTIME="${RUNTIME:-8}"
RAMP="${RAMP:-2}"
ENGINE="${ENGINE:-io_uring}"
OUT="${1:-$ROOT_DIR/microbench/results/ssd_saturation_probe_$(hostname -s)_$(date -u +%Y%m%dT%H%M%SZ)}"
if [[ -e "$OUT" ]]; then echo "Refusing to overwrite existing $OUT" >&2; exit 1; fi
[[ -r "$FILE" ]] || { echo "Missing $FILE" >&2; exit 1; }
command -v fio >/dev/null
mkdir -p "$OUT"; OUT="$(realpath "$OUT")"
SIZE=$(stat -c %s "$FILE")
DEVNAME=$(lsblk -no PKNAME "$(df --output=source "$FILE" | tail -1)" 2>/dev/null | head -1)
cat > "$OUT/input.txt" <<EOF
file=$FILE size=$SIZE
device=$DEVNAME model=$(cat /sys/block/$DEVNAME/device/model 2>/dev/null | xargs) fw=$(cat /sys/block/$DEVNAME/device/firmware_rev 2>/dev/null | xargs)
runtime=$RUNTIME ramp=$RAMP engine=$ENGINE fio=$(fio --version) kernel=$(uname -r) host=$(hostname -s)
EOF
cat "$OUT/input.txt"

run() {  # name, fio args...
    local name="$1"; shift
    mkdir "$OUT/$name"
    fio --name="$name" --filename="$FILE" --readonly --direct=1 --invalidate=0 --ioengine="$ENGINE" --time_based \
        --runtime="$RUNTIME" --ramp_time="$RAMP" --group_reporting --output-format=json \
        --output="$OUT/$name/fio.json" "$@" > /dev/null
    echo "done $name"
}
qd_split() {  # total QD -> "--numjobs=J --iodepth=D"
    local qd=$1
    if (( qd >= 4 )); then echo "--numjobs=4 --iodepth=$((qd / 4))"; else echo "--numjobs=1 --iodepth=$qd"; fi
}

# E1 / E3
for qd in 1 2 4 8 16 32 64 128 256; do
    run "E1_rand4k_qd$qd" --rw=randread --bs=4k --norandommap $(qd_split $qd)
done
# E2
for qd in 32 128; do
    for bs in 4k 8k 16k 32k 64k; do
        run "E2_rand${bs}_qd$qd" --rw=randread --bs=$bs --norandommap $(qd_split $qd)
    done
done
# E4 (sequential reader works on the second half of the file, the random readers on the first half)
HALF=$(( SIZE / 2 / 1048576 * 1048576 ))
run "E4_rand_alone" --rw=randread --bs=4k --norandommap --numjobs=4 --iodepth=32 --size=$HALF
run "E4_seq_alone" --rw=read --bs=128k --numjobs=1 --iodepth=16 --offset=$HALF --size=$HALF
mkdir "$OUT/E4_both"
fio --readonly --output-format=json --output="$OUT/E4_both/fio.json" - > /dev/null <<EOF
[global]
filename=$FILE
direct=1
invalidate=0
ioengine=$ENGINE
time_based
runtime=$RUNTIME
ramp_time=$RAMP
group_reporting
[rand]
rw=randread
bs=4k
norandommap
numjobs=4
iodepth=32
size=$HALF
[seq]
new_group
rw=read
bs=128k
numjobs=1
iodepth=16
offset=$HALF
size=$HALF
EOF
echo "done E4_both"
# E5
for r in 1M 4M 16M 64M 256M 1G 4G 16G; do
    run "E5_qd1_range$r" --rw=randread --bs=4k --norandommap --numjobs=1 --iodepth=1 --size=$r
done
run "E5_qd1_rangefull" --rw=randread --bs=4k --norandommap --numjobs=1 --iodepth=1
# E6
for r in 2M 8M 32M 128M 1G; do
    for k in 1 2 3; do
        off=$(( SIZE / 4 * k / 1048576 * 1048576 ))
        run "E6_qd16_range${r}_off$k" --rw=randread --bs=4k --norandommap --numjobs=1 --iodepth=16 \
            --offset=$off --size=$r
    done
done
run "E6_qd16_rangefull" --rw=randread --bs=4k --norandommap --numjobs=1 --iodepth=16

python3 - "$OUT" <<'PY'
import json, sys, pathlib
out = pathlib.Path(sys.argv[1]); rows = ['run,group,iops,bw_MBps,lat_mean_us,lat_p50_us,lat_p99_us']
for d in sorted(p for p in out.iterdir() if (p / 'fio.json').exists()):
    j = json.load(open(d / 'fio.json'))
    for g in j['jobs']:
        r = g['read']; cl = r.get('clat_ns', {}); pct = cl.get('percentile', {})
        lat = r.get('lat_ns', {}).get('mean', 0) / 1000
        rows.append(f"{d.name},{g['jobname']},{r['iops']:.0f},{r['bw'] / 1024:.1f},{lat:.1f},"
                    f"{pct.get('50.000000', 0) / 1000:.1f},{pct.get('99.000000', 0) / 1000:.1f}")
(out / 'summary.csv').write_text('\n'.join(rows) + '\n'); print('\n'.join(rows))
PY
