#!/usr/bin/env python3
"""Per-run block-layer latency for trace_replay/fio_addr_replay.sh output, split into hot vs other sectors.

Hot sectors = the 100 most-read 4 KiB sectors of the source DiskANN trace. Writes <sweep>/analysis.json.
"""
import collections, json, subprocess, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
from diskann_trace_to_mqsim import LINE, load_layout  # noqa: E402


def block_reads(run, disk_dev):
    rep = subprocess.run(['trace-cmd', 'report', '-t', '-i', str(run / 'block.dat')], capture_output=True, text=True, check=True)
    pending, out = collections.defaultdict(collections.deque), []
    for line in rep.stdout.splitlines():
        m = LINE.search(line)
        if not m: continue
        ts, kind, maj, mnr, rwbs, _b, sector, n = m.groups()
        if (int(maj), int(mnr)) != disk_dev or 'R' not in rwbs or 'W' in rwbs: continue
        ns, key = int(ts.replace('.', '')), (int(sector), int(n))
        if kind == 'issue': pending[key].append(ns)
        elif pending[key]: out.append((pending[key].popleft(), ns, key[0], key[1]))
    return np.array(sorted(out), dtype=np.int64)


def main():
    sweep = Path(sys.argv[1]).resolve()
    src = Path([l.split('=', 1)[1] for l in (sweep / 'input.txt').read_text().splitlines() if l.startswith('source_trace=')][0])
    local_src = sweep.parent / src.name
    disk_dev, base, extents, _ = load_layout(sweep)
    starts = np.array([e[0] for e in extents]); ends = np.array([e[1] for e in extents]); fstart = np.array([e[2] for e in extents])
    rr = np.loadtxt(local_src / 'mqsim_replay/real_requests.csv', delimiter=',', skiprows=1, dtype=np.int64)
    u, c = np.unique(rr[:, 2] // 8, return_counts=True)
    hot = u[np.argsort(c)[::-1][:100]]
    res = {}
    for run in sorted(p for p in sweep.iterdir() if p.is_dir() and '_qd' in p.name):
        r = block_reads(run, disk_dev)
        lv = r[:, 2] - base
        i = np.searchsorted(starts, lv, side='right') - 1
        ok = (i >= 0) & (lv >= starts[np.maximum(i, 0)]) & (lv + r[:, 3] <= ends[np.maximum(i, 0)])
        r, lv, i = r[ok], lv[ok], i[ok]
        sec = (fstart[i] + (lv - starts[i])) // 8
        lat = (r[:, 1] - r[:, 0]) / 1e3
        h = np.isin(sec, hot)
        # steady window: drop first and last 5% of issue time
        t = r[:, 0]; lo, hi = np.percentile(t, [5, 95]); m = (t >= lo) & (t <= hi)
        span = (t.max() - t.min()) / 1e9
        q = lambda x: {k: float(v) for k, v in zip(('p50', 'p90', 'p99'), np.percentile(x, [50, 90, 99]))} | {'mean': float(x.mean())}
        res[run.name] = {'reads': int(len(r)), 'dropped_outside_index': int((~ok).sum()), 'iops': len(r) / span,
                         'hot_share': float(h.mean()), 'all': q(lat[m]), 'hot': q(lat[m & h]), 'other': q(lat[m & ~h]),
                         'share_of_gt1ms_that_hot': float(h[m & (lat > 1000)].mean()) if (m & (lat > 1000)).any() else None}
        x = res[run.name]
        print(f"{run.name:15s} reads {x['reads']} iops {x['iops']:.0f} | hot p50 {x['hot']['p50']:.0f} p99 {x['hot']['p99']:.0f} | "
              f"other p50 {x['other']['p50']:.0f} p99 {x['other']['p99']:.0f} | hot share {100*x['hot_share']:.1f}% "
              f">1ms hot {100*(x['share_of_gt1ms_that_hot'] or 0):.1f}%")
    (sweep / 'analysis.json').write_text(json.dumps(res, indent=2))


if __name__ == '__main__':
    main()
