#!/usr/bin/env bash
# N1 on the server: functional cost of "host nominates top-k, SSD chooses W on non-busy units".
# Sweep W x k (nominate) x p (unit busy probability); every run also keeps a per-query prefetch buffer so the
# prefetch hit fraction of the unchosen nominees is reported (the buffer does not change the search).
# k = W reproduces the original beam (checked: identical results) and gives the baseline busy-round fraction.
# Functional metrics only (hops, I/Os, recall, busy-round fraction, prefetch hits); timing is not used.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="${BIN:-/home/user/DiskANN_cpp/build/apps/search_disk_index}"
PREFIX="${PREFIX:-/home/user/DiskANN/build/data/SIFT100M/disk_index_SIFT100M_base100M_R64_L100_A1.2}"
QUERY="${QUERY:-/home/user/DiskANN/build/data/SIFT100M/bigann_query.fbin}"
GT="${GT:-/home/user/abcd_data/sift100m/idx_100M.bin}"
DATA_TYPE="${DATA_TYPE:-uint8}"
THREADS="${THREADS:-16}"
SEARCH_L="${SEARCH_L:-100}"
K="${K:-10}"
CACHE_NODES="${CACHE_NODES:-0}"
UNITS="${UNITS:-96}"
SECTORS_PER_PAGE="${SECTORS_PER_PAGE:-4}"
WIDTHS=(${WIDTHS:-4 8})
KMULTS=(${KMULTS:-1 1.5 2 3})       # nominate k = W x multiplier
PROBS=(${PROBS:-0 0.1 0.2 0.3 0.4})
PREFETCH_BUFFER="${PREFETCH_BUFFER:-256}"   # sectors per query
OUT="${1:-${ROOT_DIR}/microbench/results/diskann_nominate_sweep_$(date -u +%Y%m%dT%H%M%SZ)_C${CACHE_NODES}}"

if [[ -e "$OUT" ]]; then echo "Refusing to overwrite existing $OUT" >&2; exit 1; fi
for f in "$BIN" "${PREFIX}_disk.index" "$QUERY" "$GT"; do [[ -e "$f" ]] || { echo "Missing $f" >&2; exit 1; }; done
[[ "$("$BIN" --help 2>&1 || true)" == *frontier_nominate* ]] || { echo "$BIN lacks --frontier_nominate (pull + rebuild DiskANN_cpp)" >&2; exit 1; }
mkdir -p "$OUT"
cat > "$OUT/input.txt" <<EOF
bin=$BIN
diskann_commit=$(git -C "$(dirname "$BIN")/../.." rev-parse --short HEAD 2>/dev/null || echo unknown)
prefix=$PREFIX
query=$QUERY
gt=$GT
data_type=$DATA_TYPE threads=$THREADS L=$SEARCH_L K=$K num_nodes_to_cache=$CACHE_NODES
units=$UNITS sectors_per_page=$SECTORS_PER_PAGE widths=${WIDTHS[*]} kmults=${KMULTS[*]} probs=${PROBS[*]}
prefetch_buffer=$PREFETCH_BUFFER
EOF
cat "$OUT/input.txt"
echo "run,W,k,p,mean_ios,mean_hops,recall,busy_round_pct,busy_read_pct,prefetch_issued_per_query,prefetch_hit_pct" > "$OUT/summary.csv"

for w in "${WIDTHS[@]}"; do
    for m in "${KMULTS[@]}"; do
        k=$(python3 -c "print(int(round($w * $m)))")
        for p in "${PROBS[@]}"; do
            name="W${w}_k${k}_p${p}"
            mkdir "$OUT/$name"
            "$BIN" --data_type "$DATA_TYPE" --dist_fn l2 --index_path_prefix "$PREFIX" --query_file "$QUERY" \
                --gt_file "$GT" --result_path "$OUT/$name/res" --recall_at "$K" --search_list "$SEARCH_L" \
                --num_threads "$THREADS" --num_nodes_to_cache "$CACHE_NODES" --beamwidth "$w" \
                --frontier_unit_count "$UNITS" --frontier_sectors_per_unit_page "$SECTORS_PER_PAGE" \
                --frontier_nominate "$k" --frontier_busy_prob "$p" --frontier_prefetch_buffer "$PREFETCH_BUFFER" \
                > "$OUT/$name/search.log" 2>&1 || { echo "FAILED: $name" >&2; exit 1; }
            python3 - "$OUT/$name/search.log" "$name" "$w" "$k" "$p" "$SEARCH_L" >> "$OUT/summary.csv" <<'EOF'
import sys, re
log, name, w, k, p, L = sys.argv[1:]
txt = open(log).read().splitlines()
row = [l.split() for l in txt if l.split()[:1] == [L] and len(l.split()) >= 12][-1]
nom = [l for l in txt if l.startswith('[nominate]')][-1]
kv = dict(re.findall(r'(\w+)=([\d.eE+-]+)', nom))
print(','.join([name, w, k, p, row[5], row[9], row[-1], kv['busy_round_pct'], kv['busy_read_pct'],
                kv['prefetch_issued_per_query'], kv['prefetch_hit_pct']]))
EOF
            tail -1 "$OUT/summary.csv"
        done
    done
done
du -sh "$OUT"
echo "Done: $OUT"
