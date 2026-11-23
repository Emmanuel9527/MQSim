#!/usr/bin/env bash
# E10: is the cost of mixing large reads into a saturating 4 KiB stream a per-switch cost (4 KiB fast path <-> general
# path)? Same total 8 KiB reads, delivered spread out, in bursts, or interleaved inside the 4 KiB stream.
# Read-only fio probes on an existing large file (O_DIRECT, no writes). Follow-up to ssd_controller_probe.sh (E8/E9).
#   all mixed runs: 4 KiB random reads at QD 256 (4 jobs x 64), as in E8
#   spread   second reader of 8 KiB, rate-limited and evenly spaced (rate_iops, as in E8): one switch per 8 KiB read
#   burst    second reader issues B 8 KiB reads at once (batch submit), waits for all, then sleeps: B reads per switch
#   mix      one stream: each of the 4 x 64 slots picks 4 KiB or 8 KiB at random (bssplit): the most switches
# Prediction if the cost is per switch: 4 KiB reads displaced per 8 KiB read is highest for mix, then spread, and
# drops with the burst size; if the cost is per 8 KiB read (resources held), it is about the same for all.
# Usage: realssd/ssd_switch_probe.sh OUT   (env: FILE, RUNTIME, RAMP, ENGINE)
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
cat > "$OUT/input.txt" <<EOF
file=$FILE size=$(stat -c %s "$FILE")
runtime=$RUNTIME ramp=$RAMP engine=$ENGINE fio=$(fio --version) kernel=$(uname -r) host=$(hostname -s)
EOF
cat "$OUT/input.txt"

GLOBAL="[global]
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
iodepth=64"

job() {  # name, job file text on stdin
    local name="$1"
    mkdir "$OUT/$name"
    fio --readonly --output-format=json --output="$OUT/$name/fio.json" - > /dev/null
    echo "done $name"
}
alone() {
    job "$1" <<EOF
$GLOBAL
EOF
}
spread() {  # name, rate
    job "$1" <<EOF
$GLOBAL
[mix8k]
new_group
bs=8k
numjobs=1
iodepth=32
rate_iops=$2
EOF
}
burst() {  # name, burst size, sleep between bursts (us)
    job "$1" <<EOF
$GLOBAL
[mix8k]
new_group
bs=8k
numjobs=1
iodepth=$2
iodepth_batch_submit=$2
iodepth_batch_complete_min=$2
thinktime=$3
thinktime_blocks=$2
EOF
}
mixed() {  # name, percent of 8 KiB reads (by count)
    job "$1" <<EOF
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
[mixstream]
bssplit=4k/$((100 - $2)):8k/$2
numjobs=4
iodepth=64
EOF
}

alone  "E10_alone"
spread "E10_spread_r10000" 10000
spread "E10_spread_r5000" 5000
burst  "E10_burst8_t550" 8 550
burst  "E10_burst32_t2200" 32 2200
burst  "E10_burst32_t5000" 32 5000
burst  "E10_burst128_t10000" 128 10000
mixed  "E10_mix_1pct" 1
mixed  "E10_mix_2pct" 2
alone  "E10_alone_again"

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
