#!/usr/bin/env bash
# P1 + P4 on the server: search-policy sweep (beam width x frontier unit policy x sector expansion) with recall,
# plus expansion logs for the prefetch-prediction analysis. Functional metrics only (hops, I/Os, recall); the
# timing of these runs is not used. Read-only for the SSD apart from the output files.
#
# Needs DiskANN_cpp with --frontier_unit_count / --trace_expansion_bin (vucann branch, built).
# Output per run: <OUT>/<name>/search.log, res_*; summary: <OUT>/summary.csv
# Expansion logs (P1): <OUT>/explog_W<w>/expansions.bin (+ .queries.csv) and trace_access.csv
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BIN="${BIN:-/home/user/DiskANN_cpp/build/apps/search_disk_index}"
PREFIX="${PREFIX:-/home/user/DiskANN/build/data/SIFT100M/disk_index_SIFT100M_base100M_R64_L100_A1.2}"
QUERY="${QUERY:-/home/user/DiskANN/build/data/SIFT100M/bigann_query.fbin}"
GT="${GT:-/home/user/abcd_data/sift100m/idx_100M.bin}"
DATA_TYPE="${DATA_TYPE:-uint8}"
THREADS="${THREADS:-16}"
SEARCH_L="${SEARCH_L:-100}"
K="${K:-10}"
CACHE_NODES="${CACHE_NODES:-0}"
WIDTHS=(${WIDTHS:-4 8 16 32 64 96})
UNITS="${UNITS:-96}"                 # parallel units of the calibrated T500 model (4 ch x 2 chips x 12 dies)
SECTORS_PER_PAGE="${SECTORS_PER_PAGE:-4}"  # 16 KiB page / 4 KiB sector
EXPLOG_WIDTHS=(${EXPLOG_WIDTHS:-4 8})
EXPLOG_QUERIES="${EXPLOG_QUERIES:-1000}"
OUT="${1:-${ROOT_DIR}/microbench/results/diskann_policy_sweep_$(date -u +%Y%m%dT%H%M%SZ)_C${CACHE_NODES}}"

if [[ -e "$OUT" ]]; then echo "Refusing to overwrite existing $OUT" >&2; exit 1; fi
for f in "$BIN" "${PREFIX}_disk.index" "$QUERY" "$GT"; do [[ -e "$f" ]] || { echo "Missing $f" >&2; exit 1; }; done
[[ "$("$BIN" --help 2>&1 || true)" == *frontier_unit_count* ]] || { echo "$BIN lacks --frontier_unit_count (pull + rebuild DiskANN_cpp)" >&2; exit 1; }
mkdir -p "$OUT"
cat > "$OUT/input.txt" <<EOF
bin=$BIN
diskann_commit=$(git -C "$(dirname "$BIN")/../.." rev-parse --short HEAD 2>/dev/null || echo unknown)
prefix=$PREFIX
query=$QUERY
gt=$GT
data_type=$DATA_TYPE threads=$THREADS L=$SEARCH_L K=$K num_nodes_to_cache=$CACHE_NODES
widths=${WIDTHS[*]} units=$UNITS sectors_per_page=$SECTORS_PER_PAGE
explog_widths=${EXPLOG_WIDTHS[*]} explog_queries=$EXPLOG_QUERIES
EOF
cat "$OUT/input.txt"

search() {  # name, extra args...
    local name="$1"; shift
    mkdir "$OUT/$name"
    echo "=== $name"
    "$BIN" --data_type "$DATA_TYPE" --dist_fn l2 --index_path_prefix "$PREFIX" --query_file "$QUERY" \
        --gt_file "$GT" --result_path "$OUT/$name/res" --recall_at "$K" --search_list "$SEARCH_L" \
        --num_threads "$THREADS" --num_nodes_to_cache "$CACHE_NODES" "$@" > "$OUT/$name/search.log" 2>&1 \
        || { echo "FAILED: $name (see $OUT/$name/search.log)" >&2; exit 1; }
    awk -v L="$SEARCH_L" '$1 == L && NF >= 12' "$OUT/$name/search.log" | tail -1
}

# P1: expansion logs (original policy)
for w in "${EXPLOG_WIDTHS[@]}"; do
    search "explog_W$w" --beamwidth "$w" --trace_expansion_bin "$OUT/explog_W$w/expansions.bin" \
        --trace_access_csv "$OUT/explog_W$w/trace_access.csv" --trace_max_queries "$EXPLOG_QUERIES" \
        --trace_run_label "explog_W$w"
done

# P4: policy sweep
for w in "${WIDTHS[@]}"; do
    search "base_W$w" --beamwidth "$w"
    search "unit_W$w" --beamwidth "$w" --frontier_unit_count "$UNITS" --frontier_sectors_per_unit_page "$SECTORS_PER_PAGE"
    search "sector_W$w" --beamwidth "$w" --use_sector_candidates
    search "unitsector_W$w" --beamwidth "$w" --use_sector_candidates --frontier_unit_count "$UNITS" \
        --frontier_sectors_per_unit_page "$SECTORS_PER_PAGE"
done

python3 - "$OUT" "$SEARCH_L" <<'EOF'
import sys, pathlib
out, L = pathlib.Path(sys.argv[1]), sys.argv[2]
rows = ['run,L,W,qps,mean_lat_us,p999_lat_us,mean_ios,mean_io_us,mean_cpu_us,mean_cache_hits,mean_hops,recall']
for d in sorted(p for p in out.iterdir() if (p / 'search.log').is_file()):
    line = [l.split() for l in open(d / 'search.log') if l.split()[:1] == [L] and len(l.split()) >= 12]
    if line:
        v = line[-1]
        # search_disk_index table: L W QPS mean_lat p999_lat mean_ios mean_io_us cpu_us cache_hits(?) hops ... recall
        rows.append(','.join([d.name] + v[:2] + v[2:5] + v[5:8] + [v[8], v[9], v[-1]]))
(out / 'summary.csv').write_text('\n'.join(rows) + '\n')
print('\n'.join(rows))
EOF
du -sh "$OUT"
echo "Done: $OUT"
