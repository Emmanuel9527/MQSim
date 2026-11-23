#!/usr/bin/env bash
# Is the extra cost of >4 KiB reads at saturation a per-command controller cost, or NAND / transfer work?
# Read-only fio probes on an existing large file (O_DIRECT, no writes). Follow-up to ssd_saturation_probe.sh (E1-E7).
#   E8  mixed load: 4 KiB random reads at QD 256 (saturating) plus a rate-limited second reader of 4K / 8K / 16K
#       at 10k / 20k / 40k IOPS. How many 4 KiB reads does one extra read displace?
#         per-command controller cost (from saturation IOPS: 4K 0.96 us, 8K 5.7 us, 16K 7.4 us)
#             -> one 8K displaces ~6, one 16K ~7.7 four-KiB reads
#         NAND / channel work proportional to data -> one 8K displaces <= 2, one 16K <= 4
#         4K mixed in = control: should displace ~1
#   E9  alignment at load: 16K reads inside one 16 KiB-aligned unit vs always straddling two (offset 4K);
#       8K reads inside one unit vs straddling (offset 12K); QD 32 and 128.
#         straddling as cheap as aligned -> the cost is not in NAND page sensing
#       (assumes 16 KiB-aligned file offsets map to one flash page: true for a sequentially written file on an
#        aligned partition, not guaranteed by the FTL)
# Usage: realssd/ssd_controller_probe.sh OUT   (env: FILE, RUNTIME, RAMP, ENGINE)
set -euo pipefail
FILE="${FILE:-/home/user/DiskANN/build/data/SIFT100M/disk_index_SIFT100M_base100M_R64_L100_A1.2_disk.index}"
RUNTIME="${RUNTIME:-8}"
RAMP="${RAMP:-2}"
ENGINE="${ENGINE:-io_uring}"
OUT="${1:?usage: $0 OUT}"
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
mixed() {  # name, bs of the second reader, its rate (IOPS)
    local name="$1" bs="$2" rate="$3"
    mkdir "$OUT/$name"
    fio --readonly --output-format=json --output="$OUT/$name/fio.json" - > /dev/null <<EOF
[global]
filename=$FILE
direct=1
invalidate=0
ioengine=$ENGINE
time_based
runtime=$RUNTIME
ramp_time=$RAMP
group_reporting
norandommap
rw=randread
[rand4k]
bs=4k
numjobs=4
iodepth=64
[mix$bs]
new_group
bs=$bs
numjobs=1
iodepth=32
rate_iops=$rate
EOF
    echo "done $name"
}

# E8: saturating 4K alone (same QD as in the mixed runs), then with a second reader
run "E8_rand4k_qd256_alone" --rw=randread --bs=4k --norandommap --numjobs=4 --iodepth=64
for bs in 4k 8k 16k; do
    for rate in 10000 20000 40000; do
        mixed "E8_mix${bs}_r${rate}" "$bs" "$rate"
    done
done
run "E8_rand4k_qd256_alone_again" --rw=randread --bs=4k --norandommap --numjobs=4 --iodepth=64

# E9: alignment (fio offsets are file offset + a multiple of blockalign)
for qd in 32 128; do
    run "E9_16k_aligned_qd$qd"   --rw=randread --bs=16k --blockalign=16k --norandommap --numjobs=4 --iodepth=$((qd / 4))
    run "E9_16k_straddle_qd$qd"  --rw=randread --bs=16k --blockalign=16k --offset=4k --norandommap --numjobs=4 --iodepth=$((qd / 4))
    run "E9_8k_aligned_qd$qd"    --rw=randread --bs=8k --blockalign=16k --norandommap --numjobs=4 --iodepth=$((qd / 4))
    run "E9_8k_straddle_qd$qd"   --rw=randread --bs=8k --blockalign=16k --offset=12k --norandommap --numjobs=4 --iodepth=$((qd / 4))
done

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
echo ALLDONE
