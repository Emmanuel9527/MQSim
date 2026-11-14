#!/usr/bin/env bash
# Record kernel block-layer request events while DiskANN searches a disk index, for
# comparing real SSD behaviour against an MQSim replay of the same request stream.
#
# Output (in $OUT):
#   block.dat / block.txt     trace-cmd record/report of block_rq_issue + block_rq_complete (CLOCK_MONOTONIC)
#   search.log                search_disk_index stdout/stderr (QPS, latency, mean IOs)
#   trace_stats.csv           per-query/per-iteration DiskANN trace with steady_clock ns timestamps
#   trace_access.csv          per-iteration node/sector accesses (for a later completion-dependent replay)
#   *_stats.csv               per-query counters (DISKANN_WRITE_QUERY_STATS=1)
#   layout/                   filefrag extents (before/after), LVM and partition offsets to map
#                             device sectors back to index-file offsets
#   diskstats_*.txt           /proc/diskstats snapshots around the run
#
# Read-only for the SSD apart from writing the small output files (the output directory is on the
# same disk; those writes are filtered out later by direction and by sector range).
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="${BIN:-/home/user/DiskANN_cpp/build/apps/search_disk_index}"
PREFIX="${PREFIX:-/home/user/DiskANN/build/data/SIFT100M/disk_index_SIFT100M_base100M_R64_L100_A1.2}"
QUERY="${QUERY:-/home/user/DiskANN/build/data/SIFT100M/bigann_query.fbin}"
DATA_TYPE="${DATA_TYPE:-float}"
THREADS="${THREADS:-16}"
BEAMWIDTH="${BEAMWIDTH:-4}"
SEARCH_L="${SEARCH_L:-100}"
K="${K:-10}"
CACHE_NODES="${CACHE_NODES:-0}"
DEV="${DEV:-nvme0n1}"
OUT="${1:-${ROOT_DIR}/microbench/results/diskann_trace_$(date -u +%Y%m%dT%H%M%SZ)_T${THREADS}_W${BEAMWIDTH}_L${SEARCH_L}}"

if [[ -e "$OUT" ]]; then echo "Refusing to overwrite existing $OUT" >&2; exit 1; fi
for f in "$BIN" "${PREFIX}_disk.index" "${PREFIX}_pq_compressed.bin" "${PREFIX}_pq_pivots.bin" "$QUERY"; do
    [[ -e "$f" ]] || { echo "Missing $f" >&2; exit 1; }
done
command -v trace-cmd >/dev/null && command -v filefrag >/dev/null
mkdir -p "$OUT/layout"
OUT="$(realpath "$OUT")"
sudo -v

cat > "$OUT/input.txt" <<EOF
bin=$BIN
git_commit=$(git -C "$(dirname "$BIN")/../.." log --oneline -1 2>/dev/null || echo unknown)
index_prefix=$PREFIX
query=$QUERY
data_type=$DATA_TYPE threads=$THREADS beamwidth=$BEAMWIDTH L=$SEARCH_L K=$K num_nodes_to_cache=$CACHE_NODES
device=$DEV scheduler=$(cat /sys/block/$DEV/queue/scheduler) nr_requests=$(cat /sys/block/$DEV/queue/nr_requests)
kernel=$(uname -r)
EOF
cat "$OUT/input.txt"

# Layout: file extents are in units of 512-byte sectors of the filesystem's block device (the LV).
# dm-linear table + partition start convert LV sectors to whole-disk sectors seen by block_rq_issue.
layout() {  # $1 = before|after
    filefrag -v -b512 "${PREFIX}_disk.index" > "$OUT/layout/disk_index.extents.$1.txt"
}
layout before
findmnt -T "${PREFIX}_disk.index" -o SOURCE,FSTYPE,TARGET > "$OUT/layout/mount.txt"
lsblk -o NAME,MAJ:MIN,SIZE,TYPE,PKNAME,START > "$OUT/layout/lsblk.txt"
sudo dmsetup table > "$OUT/layout/dmsetup_table.txt"
for p in /sys/block/$DEV/${DEV}p*; do echo "$(basename "$p") start=$(cat "$p/start") size=$(cat "$p/size")"; done > "$OUT/layout/partitions.txt"
cat /sys/block/$DEV/dev > "$OUT/layout/dev_majmin.txt"
cat /proc/diskstats > "$OUT/diskstats_before.txt"

echo "Recording block events (CLOCK_MONOTONIC) while DiskANN searches; sudo is needed for kernel tracing."
sudo trace-cmd record -C mono -b 65536 -o "$OUT/block.dat" \
    -e block:block_rq_issue -e block:block_rq_complete \
    sudo -u "$(id -un)" env DISKANN_WRITE_QUERY_STATS=1 \
    "$BIN" --data_type "$DATA_TYPE" --dist_fn l2 \
        --index_path_prefix "$PREFIX" --query_file "$QUERY" \
        --result_path "$OUT/res" --recall_at "$K" --search_list "$SEARCH_L" \
        --beamwidth "$BEAMWIDTH" --num_threads "$THREADS" --num_nodes_to_cache "$CACHE_NODES" \
        --trace_stats_csv "$OUT/trace_stats.csv" --trace_access_csv "$OUT/trace_access.csv" \
        --trace_sample_rate 1 --trace_max_queries 0 --trace_run_label "T${THREADS}_W${BEAMWIDTH}_L${SEARCH_L}" \
    2>&1 | tee "$OUT/search.log"

cat /proc/diskstats > "$OUT/diskstats_after.txt"
layout after
sudo trace-cmd report -i "$OUT/block.dat" > "$OUT/block.txt" 2> "$OUT/report.log"
sudo chown -R "$(id -un)" "$OUT"
if cmp -s "$OUT/layout/disk_index.extents.before.txt" "$OUT/layout/disk_index.extents.after.txt"; then
    echo "Index extents unchanged during the run."
else
    echo "WARNING: index extents changed during the run." >&2
fi
echo "LOST-event lines: $(grep -ci 'lost' "$OUT/report.log" "$OUT/block.txt" | paste -sd' ')"
echo "block events: $(wc -l < "$OUT/block.txt")"
du -sh "$OUT"
echo "Done: $OUT"
