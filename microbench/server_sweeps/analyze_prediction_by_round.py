#!/usr/bin/env python3
"""P1 by search phase: how the next-round reads split into "old host candidates" and "new neighbours" over a query.

Same input and replay as server_sweeps/analyze_prefetch_prediction.py (explog dir, original policy, no cache). For every round r
that has a round r+1, each read of round r+1 is classed:
  old  its node was already an unexpanded candidate in the host list when round r started (the host hint H,
       k = W, covers exactly these: ranks W+1..2W of the list)
  new  its node was inserted from round r's neighbours
Rounds are grouped by absolute index r and by search progress r / (rounds of that query) in 10 bins.
Also reports the H coverage at sector granularity (k = W), which is the P1 headline number.
Usage: server_sweeps/analyze_prediction_by_round.py <explog_dir> --W 4 [--L 100] [--out prefix]
Writes <out>.json and <out>.png (default out: <explog_dir>/prediction_by_round)
"""
import argparse, collections, csv, json
from pathlib import Path
import numpy as np
import sys as _sys, pathlib as _pl; _sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1] / 'common'))
from explog import REC, CandList, node_sector_fn


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('dir', type=Path)
    ap.add_argument('--W', type=int, required=True)
    ap.add_argument('--L', type=int, default=100)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    d = a.dir.resolve(); W, L = a.W, a.L
    out = Path(a.out) if a.out else d / 'prediction_by_round'
    qrows = list(csv.DictReader(open(d / 'expansions.bin.queries.csv')))
    nrec = sum(int(r['records']) for r in qrows)
    rec = np.fromfile(d / 'expansions.bin', dtype=REC, count=nrec)
    access = collections.defaultdict(list); flat = []
    with open(d / 'trace_access.csv') as f:
        for r in csv.DictReader(f):
            access[(int(r['query_id']), int(r['iteration']))].append(int(r['node_id']))
            if len(flat) < 200000: flat.append((int(r['node_id']), int(r['sector_id'])))
    nps = node_sector_fn(flat); sec = lambda i: 1 + i // nps
    by_r = collections.defaultdict(lambda: np.zeros(4))      # [reads, old, sector-covered by H, rounds]
    by_bin = collections.defaultdict(lambda: np.zeros(4))
    mism = 0; pos = 0; iters = []
    for r in qrows:
        q, n = int(r['query_id']), int(r['records'])
        x = rec[pos:pos + n]; pos += n
        by_it = collections.defaultdict(list)
        for t in x: by_it[int(t['it'])].append(t)
        cl = CandList(L); cl.insert(float(r['start_pq_distance']), int(r['start_node']))
        F, H = [], []
        for it in range(int(r['iterations'])):
            f = cl.take(W)
            if f != access.get((q, it), []): mism += 1
            F.append(f); H.append(cl.unexpanded(W))
            for t in by_it.get(it, []):
                if t['f'] & 1: cl.insert(float(t['d']), int(t['nbr']))
        R = len(F); iters.append(R)
        for it in range(R - 1):
            tgt = F[it + 1]
            if not tgt: continue
            hs, hsec = set(H[it]), set(sec(i) for i in H[it])
            v = np.array([len(tgt), sum(i in hs for i in tgt), sum(sec(i) in hsec for i in tgt), 1])
            by_r[it] += v
            by_bin[min(9, int(10 * it / R))] += v
    res = dict(dir=str(d), W=W, L=L, queries=len(qrows), replay_mismatches=mism,
               rounds_mean=float(np.mean(iters)), rounds_p10=int(np.percentile(iters, 10)),
               rounds_p90=int(np.percentile(iters, 90)),
               by_round=[dict(r=k, rounds=int(v[3]), old_pct=100 * v[1] / v[0], h_sector_cov_pct=100 * v[2] / v[0])
                         for k, v in sorted(by_r.items())],
               by_progress=[dict(bin=k, rounds=int(v[3]), old_pct=100 * v[1] / v[0], h_sector_cov_pct=100 * v[2] / v[0])
                            for k, v in sorted(by_bin.items())])
    tot = sum(by_r.values())
    res['overall_old_pct'] = 100 * tot[1] / tot[0]; res['overall_h_sector_cov_pct'] = 100 * tot[2] / tot[0]
    out.with_suffix('.json').write_text(json.dumps(res, indent=2))
    print(f"W={W}: {len(qrows)} queries, rounds mean {res['rounds_mean']:.1f}, replay mismatches {mism}, "
          f"old {res['overall_old_pct']:.1f}%, H sector coverage {res['overall_h_sector_cov_pct']:.1f}%")
    print('progress bin  rounds  old%  H-sector%')
    for b in res['by_progress']:
        print(f"{b['bin'] * 10:3d}-{b['bin'] * 10 + 10:3d}%  {b['rounds']:6d}  {b['old_pct']:5.1f}  {b['h_sector_cov_pct']:5.1f}")
    print('round r  rounds  old%')
    for b in res['by_round']:
        if b['r'] < 12 or b['r'] % 5 == 0: print(f"{b['r']:7d}  {b['rounds']:6d}  {b['old_pct']:5.1f}")
    return res


if __name__ == '__main__':
    main()
