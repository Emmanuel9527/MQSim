#!/usr/bin/env bash
# E11: does batching 8 KiB reads help more when every command goes through ONE NVMe submission queue?
# In E10 the jobs ran on different CPUs, i.e. different hardware queues; the controller arbitrates between queues, so
# a burst of 8 KiB reads may still reach it interleaved with 4 KiB reads. Here the same E10 patterns run in three
# placements (read-only fio on an existing large file, O_DIRECT, no writes):
#   sep    4 KiB jobs pinned to CPUs 4-7 (one each), 8 KiB job pinned to CPU 8: separate hardware queues
#   same   all jobs pinned to CPU 2: one hardware queue (one CPU may not reach the SSD's saturation by itself;
#          the same-placement 'alone' run is the baseline for that placement)
#   sqp    io_uring SQPOLL: every job's submission thread pinned to CPU 2 (one queue), user threads unpinned
# Patterns per placement: 4 KiB alone; + 8 KiB evenly spaced at 10k IOPS; + 8 KiB in bursts of 32 and of 128.
# If the residual cost in E10 came from bursts being interleaved across queues, bursts should displace far fewer
# 4 KiB reads per 8 KiB read in 'same'/'sqp' than in 'sep'; if it is a cost of each large read, no change.
# Usage: realssd/ssd_queue_probe.sh OUT   (env: FILE, RUNTIME, RAMP)
set -euo pipefail
FILE="${FILE:-/home/user/DiskANN/build/data/SIFT100M/disk_index_SIFT100M_base100M_R64_L100_A1.2_disk.index}"
RUNTIME="${RUNTIME:-8}"
RAMP="${RAMP:-2}"
OUT="${1:?usage: $0 OUT}"
if [[ -e "$OUT" ]]; then echo "Refusing to overwrite existing $OUT" >&2; exit 1; fi
[[ -r "$FILE" ]] || { echo "Missing $FILE" >&2; exit 1; }
command -v fio >/dev/null
(( $(nproc) >= 9 )) || { echo "need at least 9 CPUs (have $(nproc))" >&2; exit 1; }
mkdir -p "$OUT"; OUT="$(realpath "$OUT")"
DEV=$(lsblk -no PKNAME "$(df --output=source "$FILE" | tail -1)" 2>/dev/null | head -1)
[[ -n "$DEV" && -d /sys/block/$DEV/mq ]] || DEV=$(ls /sys/block | grep -m1 '^nvme.n1$')
{
    echo "file=$FILE size=$(stat -c %s "$FILE")"
    echo "runtime=$RUNTIME ramp=$RAMP fio=$(fio --version) kernel=$(uname -r) host=$(hostname -s) nproc=$(nproc) device=$DEV"
    echo "hardware queues ($(ls /sys/block/$DEV/mq | wc -l)), hctx: cpu_list"
    for q in /sys/block/$DEV/mq/*/; do echo "  $(basename "$q"): $(cat "$q/cpu_list")"; done
} > "$OUT/input.txt"
cat "$OUT/input.txt"

common() {  # global section
    cat <<EOF
[global]
filename=$FILE
direct=1
invalidate=0
ioengine=io_uring
time_based
runtime=$RUNTIME
ramp_time=$RAMP
group_reporting
norandommap
rw=randread
EOF
}
pin4k() {  # placement -> options of the 4 KiB group
    case $1 in
        sep)  printf 'cpus_allowed=4-7\ncpus_allowed_policy=split\n' ;;
        same) printf 'cpus_allowed=2\ncpus_allowed_policy=shared\n' ;;
        sqp)  printf 'sqthread_poll=1\nsqthread_poll_cpu=2\n' ;;
    esac
}
pin8k() {
    case $1 in
        sep)  printf 'cpus_allowed=8\n' ;;
        same) printf 'cpus_allowed=2\n' ;;
        sqp)  printf 'sqthread_poll=1\nsqthread_poll_cpu=2\n' ;;
    esac
}
run() {  # name, placement, pattern
    local name="$1" pl="$2" pat="$3"
    mkdir "$OUT/$name"
    {
        common
        printf '[rand4k]\nbs=4k\nnumjobs=4\niodepth=64\n'; pin4k "$pl"
        case $pat in
            alone) ;;
            spread) printf '[mix8k]\nnew_group\nbs=8k\nnumjobs=1\niodepth=32\nrate_iops=10000\n'; pin8k "$pl" ;;
            burst32) printf '[mix8k]\nnew_group\nbs=8k\nnumjobs=1\niodepth=32\niodepth_batch_submit=32\niodepth_batch_complete_min=32\nthinktime=2200\nthinktime_blocks=32\n'; pin8k "$pl" ;;
            burst128) printf '[mix8k]\nnew_group\nbs=8k\nnumjobs=1\niodepth=128\niodepth_batch_submit=128\niodepth_batch_complete_min=128\nthinktime=10000\nthinktime_blocks=128\n'; pin8k "$pl" ;;
        esac
    } > "$OUT/$name/job.fio"
    fio --readonly --output-format=json --output="$OUT/$name/fio.json" "$OUT/$name/job.fio" > /dev/null
    echo "done $name"
}

for pl in sep same sqp; do
    for pat in alone spread burst32 burst128; do
        run "E11_${pl}_${pat}" "$pl" "$pat"
    done
done
run "E11_sep_alone_again" sep alone

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
