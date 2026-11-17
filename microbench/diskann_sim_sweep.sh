#!/usr/bin/env bash
# N2 on the server: event-driven simulation (sim_search_disk_index) of the nominate/choose and prefetch policies
# under concurrency. Host profiles: microbench/configs/host_profiles/T<t>_W<w>.txt (measured, real T500 traces).
# The "base" policy of every config validates the simplified SSD model against the MQSim closed-loop replays.
# Simulated times only; the real I/O of the simulator (reading node contents) does not affect the results.
# CACHE_NODES=N: node cache of N nodes (BFS around the medoid, as search_disk_index); use host profiles measured with
#   the same cache (PROFILES=microbench/configs/host_profiles_C100000 for N=100000).
# SEEDS="1 2 3": one run per seed (run names get a _s<seed> suffix); default: the single SEED.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SIM="${SIM:-/home/user/DiskANN_cpp/build/apps/sim_search_disk_index}"
PREFIX="${PREFIX:-/home/user/DiskANN/build/data/SIFT100M/disk_index_SIFT100M_base100M_R64_L100_A1.2}"
QUERY="${QUERY:-/home/user/DiskANN/build/data/SIFT100M/bigann_query.fbin}"
GT="${GT:-/home/user/abcd_data/sift100m/idx_100M.bin}"
DATA_TYPE="${DATA_TYPE:-uint8}"
CONFIGS=(${CONFIGS:-T8_W4 T16_W4 T32_W8 T48_W8 T32_W16 T48_W16})
POLICIES=(${POLICIES:-base avoid1.5 avoid2 avoid3 avoid2_wait35 avoid2_pf4k avoid2_pf16k hint2_pf4k})
NUM_QUERIES="${NUM_QUERIES:-0}"   # 0 = all 10,000
PARALLEL="${PARALLEL:-4}"         # concurrent simulator processes (each loads the PQ codes, ~3 GB)
SEED="${SEED:-1}"
SEEDS=(${SEEDS:-})
CACHE_NODES="${CACHE_NODES:-0}"
PROFILES="${PROFILES:-$ROOT_DIR/microbench/configs/host_profiles}"
PROFILES="$(realpath "$PROFILES")"
OUT="${1:-${ROOT_DIR}/microbench/results/diskann_sim_sweep_$(date -u +%Y%m%dT%H%M%SZ)}"

if [[ -e "$OUT" ]]; then echo "Refusing to overwrite existing $OUT" >&2; exit 1; fi
if [[ "$CACHE_NODES" != 0 ]] && ! "$SIM" --help 2>&1 | grep -q num_nodes_to_cache; then
    echo "$SIM lacks --num_nodes_to_cache (pull + rebuild DiskANN_cpp)" >&2; exit 1; fi
for f in "$SIM" "${PREFIX}_disk.index" "$QUERY" "$GT"; do [[ -e "$f" ]] || { echo "Missing $f" >&2; exit 1; }; done
mkdir -p "$OUT"
cat > "$OUT/input.txt" <<EOF
sim=$SIM
diskann_commit=$(git -C "$(dirname "$SIM")/../.." rev-parse --short HEAD 2>/dev/null || echo unknown)
mqsim_commit=$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)
prefix=$PREFIX
query=$QUERY
gt=$GT
configs=${CONFIGS[*]}
policies=${POLICIES[*]}
num_queries=$NUM_QUERIES seed=$SEED seeds=${SEEDS[*]:-} parallel=$PARALLEL
num_nodes_to_cache=$CACHE_NODES profiles=$PROFILES
EOF
cat "$OUT/input.txt"

policy_args() {  # policy W -> extra args
    local p="$1" w="$2"
    case "$p" in
        base) echo "" ;;
        avoid1.5) echo "--nominate $(( w * 3 / 2 ))" ;;
        avoid2) echo "--nominate $(( w * 2 ))" ;;
        avoid3) echo "--nominate $(( w * 3 ))" ;;
        avoid2_wait35) echo "--nominate $(( w * 2 )) --wait_threshold_us 35" ;;
        avoid2_pf4k) echo "--nominate $(( w * 2 )) --prefetch_buffer 4096" ;;
        avoid2_pf16k) echo "--nominate $(( w * 2 )) --prefetch_buffer 16384" ;;
        hint2_pf4k) echo "--nominate $(( w * 2 )) --no_avoid_busy --prefetch_buffer 4096" ;;
        *) echo "unknown policy $p" >&2; exit 1 ;;
    esac
}

: > "$OUT/jobs.txt"
for c in "${CONFIGS[@]}"; do
    t="${c%_W*}"; t="${t#T}"; w="${c#*_W}"
    [[ -f "$PROFILES/$c.txt" ]] || { echo "Missing host profile $PROFILES/$c.txt" >&2; exit 1; }
    for p in "${POLICIES[@]}"; do
        for sd in ${SEEDS[@]:-$SEED}; do
            name="${c}_${p}"; [[ ${#SEEDS[@]} -gt 0 ]] && name="${name}_s${sd}"
            echo "$SIM --data_type $DATA_TYPE --index_path_prefix $PREFIX --query_file $QUERY --gt_file $GT -K 10 -L 100 -W $w -T $t --num_queries $NUM_QUERIES --seed $sd $( [[ "$CACHE_NODES" != 0 ]] && echo "--num_nodes_to_cache $CACHE_NODES" ) --host_profile $PROFILES/$c.txt --out $OUT/$name $(policy_args "$p" "$w") > $OUT/$name.log 2>&1 && echo done $name || echo FAILED $name" >> "$OUT/jobs.txt"
        done
    done
done
echo "$(wc -l < "$OUT/jobs.txt") runs, $PARALLEL in parallel"
xargs -P "$PARALLEL" -I{} bash -c '{}' < "$OUT/jobs.txt"

python3 - "$OUT" <<'EOF'
import sys, pathlib
out = pathlib.Path(sys.argv[1])
keys = ['qps', 'latency_mean_us', 'latency_p50_us', 'latency_p99_us', 'rounds_mean', 'reads_mean', 'recall', 'round_mean_us',
        'round_p99_us', 'read_wait_mean_us', 'read_latency_mean_us', 'buffer_hits', 'prefetch_waits', 'prefetch_issued',
        'prefetch_dropped', 'critical_reads', 'unit_utilization', 'seed', 'num_nodes_to_cache', 'cache_hits',
        'cache_only_rounds']
rows = ['config,policy,' + ','.join(keys)]
for f in sorted(out.glob('*_summary.txt')):
    kv = dict(l.strip().split('=', 1) for l in open(f) if '=' in l)
    name = f.name[:-len('_summary.txt')]
    cfg, pol = name.split('_', 2)[0] + '_' + name.split('_', 2)[1], name.split('_', 2)[2]
    if kv.get('seed') and pol.endswith('_s' + kv['seed']) and '_s' + kv['seed'] != pol: pol = pol[:-len('_s' + kv['seed'])]
    rows.append(','.join([cfg, pol] + [kv.get(k, '') for k in keys]))
(out / 'summary.csv').write_text('\n'.join(rows) + '\n')
print('\n'.join(rows))
EOF
grep -l FAILED "$OUT"/*.log 2>/dev/null || true
echo "Done: $OUT"
