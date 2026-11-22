#!/usr/bin/env bash
# Real T500 check of the hot-spot finding (reports/2026-11-16_pipeline_ladder_e28.md, section 2): how much does the
# DiskANN node cache alone raise QPS, per load level? Plain search_disk_index runs (no tracing), wall-clock timing.
# Simulator prediction (cache in sectors; 1 sector = 10 nodes), QPS vs no cache:
#   T16 W4: 1k nodes +6%, 10k +8%, 100k +9%, 1M +10%      T48 W8: 1k +34%, 10k +38%, 100k +43%, 1M +46%
# Usage: server_sweeps/diskann_cache_sweep.sh [out_dir]
# Env: CONFIGS="T8_W4 T16_W4 T32_W8 T48_W8"  CACHES="0 1000 10000 100000 1000000"  REPEATS=2
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BIN="${BIN:-/home/user/DiskANN_cpp/build/apps/search_disk_index}"
PREFIX="${PREFIX:-/home/user/DiskANN/build/data/SIFT100M/disk_index_SIFT100M_base100M_R64_L100_A1.2}"
QUERY="${QUERY:-/home/user/DiskANN/build/data/SIFT100M/bigann_query.fbin}"
GT="${GT:-/home/user/abcd_data/sift100m/idx_100M.bin}"
DATA_TYPE="${DATA_TYPE:-uint8}"
SEARCH_L="${SEARCH_L:-100}"
K="${K:-10}"
CONFIGS=(${CONFIGS:-T8_W4 T16_W4 T32_W8 T48_W8})
CACHES=(${CACHES:-0 1000 10000 100000 1000000})
REPEATS="${REPEATS:-2}"
OUT="${1:-${ROOT_DIR}/microbench/results/diskann_cache_sweep_$(date -u +%Y%m%dT%H%M%SZ)}"

if [[ -e "$OUT" ]]; then echo "Refusing to overwrite existing $OUT" >&2; exit 1; fi
for f in "$BIN" "${PREFIX}_disk.index" "$QUERY" "$GT"; do [[ -e "$f" ]] || { echo "Missing $f" >&2; exit 1; }; done
mkdir -p "$OUT"
cat > "$OUT/input.txt" <<EOF
bin=$BIN
diskann_commit=$(git -C "$(dirname "$BIN")/../.." rev-parse --short HEAD 2>/dev/null || echo unknown)
prefix=$PREFIX
sample_file=$([[ -e "${PREFIX}_sample_data.bin" ]] && echo present || echo "absent (cache = BFS levels from the medoid)")
query=$QUERY
data_type=$DATA_TYPE L=$SEARCH_L K=$K
configs=${CONFIGS[*]} caches=${CACHES[*]} repeats=$REPEATS
date=$(date -u +%FT%TZ) host=$(hostname) kernel=$(uname -r)
EOF
cat "$OUT/input.txt"

for rep in $(seq 1 "$REPEATS"); do
    for c in "${CONFIGS[@]}"; do
        t="${c%_W*}"; t="${t#T}"; w="${c#*_W}"
        for n in "${CACHES[@]}"; do
            name="${c}_C${n}_r${rep}"
            mkdir "$OUT/$name"
            "$BIN" --data_type "$DATA_TYPE" --dist_fn l2 --index_path_prefix "$PREFIX" --query_file "$QUERY" \
                --gt_file "$GT" --result_path "$OUT/$name/res" --recall_at "$K" --search_list "$SEARCH_L" \
                --beamwidth "$w" --num_threads "$t" --num_nodes_to_cache "$n" > "$OUT/$name/search.log" 2>&1 \
                || { echo "FAILED: $name (see $OUT/$name/search.log)" >&2; exit 1; }
            rm -f "$OUT/$name"/res*.bin
            echo "$name $(awk -v L="$SEARCH_L" '$1 == L && NF >= 12' "$OUT/$name/search.log" | tail -1)"
        done
    done
done

python3 - "$OUT" "$SEARCH_L" <<'EOF'
import sys, pathlib, collections
out, L = pathlib.Path(sys.argv[1]), sys.argv[2]
rows = ['config,cache_nodes,repeat,qps,mean_lat_us,p999_lat_us,mean_ios,mean_io_us,cpu_s,mean_cache_hits,mean_hops,cache_hit_rate,recall']
acc = collections.defaultdict(list)
for d in sorted(p for p in out.iterdir() if (p / 'search.log').is_file()):
    line = [l.split() for l in open(d / 'search.log') if l.split()[:1] == [L] and len(l.split()) >= 12]
    if not line: continue
    v = line[-1]; cfg, cache, rep = d.name.rsplit('_', 2)
    rows.append(','.join([cfg, cache[1:], rep[1:]] + v[2:12]))
    acc[(cfg, int(cache[1:]))].append((float(v[2]), float(v[3]), float(v[5]), float(v[11])))
(out / 'summary.csv').write_text('\n'.join(rows) + '\n')
print('\nconfig    cache_nodes   QPS(mean)   vs_no_cache   mean_lat_us   mean_ios   recall   QPS per repeat')
for (cfg, n), v in sorted(acc.items(), key=lambda kv: (int(kv[0][0].split('_')[0][1:]), int(kv[0][0].split('W')[1]), kv[0][1])):
    q = sum(x[0] for x in v) / len(v); b = acc.get((cfg, 0))
    rel = f"{100 * (q / (sum(x[0] for x in b) / len(b)) - 1):+.1f}%" if b else ''
    print(f"{cfg:9s} {n:11d} {q:11.0f} {rel:>13s} {sum(x[1] for x in v) / len(v):13.0f} {sum(x[2] for x in v) / len(v):10.1f} "
          f"{v[0][3]:8.2f}   {' '.join(f'{x[0]:.0f}' for x in v)}")
EOF
du -sh "$OUT"
echo "Done: $OUT"
