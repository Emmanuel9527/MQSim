#!/usr/bin/env python3
"""Can a 'dead' read (expansion inserts nothing into the candidate list) be predicted BEFORE it is issued?
Plain DiskANN search (graphsim graph, PQ32x8 host code). For every read v, features known before the read:
  seen      how many already-expanded nodes list v as a neighbour (host knows this: reverse edges it has seen)
  rel       v's host distance / K-th candidate distance
  round     round index; after_conv: rounds since the top-K (host-distance view) last changed
Label: dead (nothing inserted), split into all_visited (every neighbour already visited) vs new_but_far.
Writes per-read rows (npz) + prints AUCs and skip curves.  Usage: ssdpq/analyze_dead_predictors.py --out f.npz
"""
import argparse, time
from pathlib import Path
import numpy as np
import coarse_quant as cq
import graphsim as gs

ap = argparse.ArgumentParser(); ap.add_argument('--W', type=int, default=4); ap.add_argument('--L', type=int, default=100)
ap.add_argument('--K', type=int, default=10); ap.add_argument('--queries', type=int, default=1000)
ap.add_argument('--out', type=Path, required=True); a = ap.parse_args()
g = gs.Graph(); base = cq.load_base(); Q = cq.load_query()
qz = cq.make('PQ32x8'); z = np.load('results/autonomous_20261122/codebooks/PQ32x8.npz')
for k in z.files: setattr(qz, k, z[k])
qz.ds = qz.cb.shape[2]; host = gs.Codes(qz, base)
cols = {k: [] for k in ['q', 'seen', 'rel', 'round', 'after_conv', 'dead', 'allvis', 'nnew', 'inres']}
t0 = time.time()
for qi in range(a.queries):
    q = Q[qi]; visited = {g.medoid}; expanded = {}; seen = {}
    cl = gs.CL(a.L); cl.insert(float(host.dists(q, np.array([g.medoid]))[0]), g.medoid)
    rnd = 0; prev_top = None; last_chg = 0; rows = []
    while True:
        F = [i for i in cl.take(a.W) if i not in expanded]
        if not F:
            if all(cl.e): break
            continue
        rnd += 1
        top = tuple(j for _, j in cl.k[:a.K])
        if top != prev_top: last_chg = rnd; prev_top = top
        dF = {jj: dd for dd, jj in cl.k if jj in F}; kth = cl.k[min(a.K, len(cl.k)) - 1][0]
        for i in F:
            vec, nb = g.node(i)
            nbn = np.array([j for j in nb if j not in visited], dtype=np.int64)
            ins = 0
            if len(nbn):
                visited.update(nbn.tolist())
                for dd, j in zip(host.dists(q, nbn), nbn): ins += cl.insert(float(dd), int(j))
            rows.append((seen.get(i, 0), dF[i] / max(kth, 1e-9), rnd, rnd - last_chg, ins == 0, len(nbn) == 0, len(nbn), i))
            expanded[i] = float(((vec - q) ** 2).sum())
            for j in nb.tolist(): seen[j] = seen.get(j, 0) + 1
    res = set(sorted(expanded, key=expanded.get)[:a.K])
    for r in rows:
        for k, v in zip(['seen', 'rel', 'round', 'after_conv', 'dead', 'allvis', 'nnew'], r[:7]): cols[k].append(v)
        cols['q'].append(qi); cols['inres'].append(r[7] in res)
C = {k: np.array(v) for k, v in cols.items()}; np.savez(a.out, **C)

def auc(score, y):
    o = np.argsort(score, kind='stable'); r = np.empty(len(o)); r[o] = np.arange(1, len(o) + 1)
    p = y.sum(); return (r[y].sum() - p * (p + 1) / 2) / (p * (len(y) - p))
y = C['dead']; n = len(y) / a.queries
print(f"W{a.W} L{a.L}: reads/query {n:.1f}, dead {y.mean():.3f} (all neighbours visited {C['allvis'].mean():.3f}), "
      f"dead∧result {(y & C['inres']).sum() / a.queries:.2f}/query, {time.time() - t0:.0f}s")
for f in ['seen', 'rel', 'round', 'after_conv']: print(f"  AUC dead vs {f}: {auc(C[f].astype(float), y):.3f}")
print("  skip rule seen>=s: skipped/query, of which dead, result reads skipped/query")
for s in [2, 3, 4, 5, 6, 8, 10]:
    m = C['seen'] >= s
    print(f"   s={s:2d}: {m.sum() / a.queries:5.1f}  precision {y[m].mean() if m.any() else 0:.3f}  result hit {(m & C['inres']).sum() / a.queries:.2f}")
