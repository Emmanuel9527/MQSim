#!/usr/bin/env python3
"""Host time profile for sim_search_disk_index (N2) from a closed-loop schedule (trace_replay/build_closed_loop_schedule.py).

pre_us      mean offset of a round's first read after read_begin
spacing_us  mean gap between consecutive reads of a round
post_us     mean last block completion -> read_end
first_gap_us  gaps of each query's first round (previous read_end -> read_begin, includes query setup)
gap_us        gaps of the other rounds (host compute between rounds)
Usage: trace_replay/make_host_profile.py <trace_dir> [out] [--max-samples N]  (default out: <trace_dir>/closed_loop/host_profile.txt;
       gap samples are uniformly subsampled to at most N each, seeded, default 20000)
"""
import sys
from pathlib import Path
import numpy as np

args = [a for a in sys.argv[1:] if not a.startswith('--max-samples')]
max_samples = int(next((a.split('=')[1] for a in sys.argv[1:] if a.startswith('--max-samples=')), 20000))
trace = Path(args[0])
out = Path(args[1]) if len(args) > 1 else trace / 'closed_loop' / 'host_profile.txt'
first, other, post, off0, spacing = [], [], [], [], []
last_query = {}
for line in open(trace / 'closed_loop' / 'schedule.txt'):
    if line.startswith('#'):
        continue
    x = line.split()
    w, q, gap, pst, n = int(x[0]), int(x[1]), int(x[2]), int(x[3]), int(x[4])
    offs = [int(x[5 + 3 * i]) for i in range(n)]
    seen = w in last_query
    is_first = last_query.get(w) != q
    last_query[w] = q
    if seen:  # a worker's very first round is measured from the trace start, not a host gap
        (first if is_first else other).append(gap / 1e3)
    post.append(pst / 1e3)
    if offs:
        off0.append(offs[0] / 1e3)
        spacing += list(np.diff(offs) / 1e3)
rng = np.random.default_rng(0)
sub = lambda v: list(rng.choice(v, max_samples, replace=False)) if len(v) > max_samples else v
first_s, other_s = sub(first), sub(other)
with open(out, 'w') as f:
    f.write(f'# host profile from {trace.name}\n')
    f.write(f'pre_us {np.mean(off0):.3f}\nspacing_us {np.mean(spacing) if spacing else 0:.3f}\npost_us {np.mean(post):.3f}\n')
    f.write('first_gap_us ' + ' '.join(f'{v:.2f}' for v in first_s) + '\n')
    f.write('gap_us ' + ' '.join(f'{v:.2f}' for v in other_s) + '\n')
print(f'{out}: pre {np.mean(off0):.2f} spacing {np.mean(spacing):.2f} post {np.mean(post):.2f} us; '
      f'first-round gap median {np.median(first):.1f} us ({len(first)}), other gap median {np.median(other):.1f} us ({len(other)})')
