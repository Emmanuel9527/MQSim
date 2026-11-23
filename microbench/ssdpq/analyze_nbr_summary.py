#!/usr/bin/env python3
"""Could a per-node 'neighbourhood summary' kept in SSD DRAM tell, before the read, that v's expansion is dead?
Plain DiskANN search (graphsim graph, PQ32x8 host). For each read v (exact L2, squared):
  tail      L-th candidate distance (host) at the moment of the read
  min_new   min host distance over v's not-yet-visited neighbours  (dead  <=>  min_new >= tail)
  lb_c{k}   lower bound on min_j ||q - x_j|| from k centroids of v's neighbours + per-centroid radius:
            max(0, ||q - c|| - r)^2, min over the k centroids (summary cost: k vectors + k radii per node)
Prints: fraction of dead reads that each bound certifies (lb >= tail -> provably dead, safe to skip).
Usage: ssdpq/analyze_nbr_summary.py --out f.npz [--queries 200]
"""
import argparse
from pathlib import Path
import numpy as np
import coarse_quant as cq
import graphsim as gs

ap = argparse.ArgumentParser(); ap.add_argument('--W', type=int, default=4); ap.add_argument('--L', type=int, default=100)
ap.add_argument('--queries', type=int, default=200); ap.add_argument('--out', type=Path, required=True); a = ap.parse_args()
g = gs.Graph(); base = cq.load_base(); Q = cq.load_query()
qz = cq.make('PQ32x8'); z = np.load('results/autonomous_20261122/codebooks/PQ32x8.npz')
for k in z.files: setattr(qz, k, z[k])
qz.ds = qz.cb.shape[2]; host = gs.ServerCodes() if gs.SERVER else gs.Codes(qz, base)

def km(X, k, it=8):
    k = min(k, len(X))
    c = X[np.random.default_rng(0).choice(len(X), k, replace=False)]
    for _ in range(it):
        lab = ((X[:, None] - c[None]) ** 2).sum(-1).argmin(1)
        c = np.stack([X[lab == j].mean(0) if (lab == j).any() else c[j] for j in range(k)])
    lab = ((X[:, None] - c[None]) ** 2).sum(-1).argmin(1)
    r = np.array([np.sqrt(((X[lab == j] - c[j]) ** 2).sum(-1).max()) if (lab == j).any() else 0 for j in range(k)])
    return c, r

R = {k: [] for k in ['tail', 'min_new', 'min_all_exact', 'lb_c1', 'lb_c2', 'lb_c4', 'd_v']}
for qi in range(a.queries):
    q = Q[qi]; visited = {g.medoid}; expanded = set()
    cl = gs.CL(a.L); cl.insert(float(host.dists(q, np.array([g.medoid]))[0]), g.medoid)
    while True:
        F = [i for i in cl.take(a.W) if i not in expanded]
        if not F:
            if all(cl.e): break
            continue
        for i in F:
            vec, nb = g.node(i); expanded.add(i)
            X = np.asarray(base[np.sort(nb)], dtype=np.float32)
            tail = cl.tail()
            nbn = np.array([j for j in nb if j not in visited], dtype=np.int64)
            dn = host.dists(q, nbn) if len(nbn) else np.array([np.inf])
            R['tail'].append(tail); R['min_new'].append(float(dn.min()))
            R['min_all_exact'].append(float(((X - q) ** 2).sum(-1).min())); R['d_v'].append(float(((vec - q) ** 2).sum()))
            for k in (1, 2, 4):
                c, r = km(X, k)
                R[f'lb_c{k}'].append(float((np.maximum(0, np.sqrt(((c - q) ** 2).sum(-1)) - r) ** 2).min()))
            if len(nbn):
                visited.update(nbn.tolist())
                for dd, j in zip(dn, nbn): cl.insert(float(dd), int(j))
R = {k: np.array(v) for k, v in R.items()}; np.savez(a.out, **R)
dead = R['min_new'] >= R['tail']; n = len(dead)
print(f"W{a.W} L{a.L}, {a.queries} q: reads {n / a.queries:.1f}/q, dead {dead.mean():.3f}")
print(f"  min over ALL neighbours (exact) >= tail: {(R['min_all_exact'] >= R['tail']).mean():.3f} of reads "
      f"(i.e. dead even ignoring 'visited')")
for k in (1, 2, 4):
    cert = R[f'lb_c{k}'] >= R['tail']
    print(f"  {k} centroid(s)+radius certify dead: {cert.mean():.4f} of reads ({cert[dead].mean():.3f} of dead); "
          f"median lb/tail {np.median(R[f'lb_c{k}'] / R['tail']):.3f}")
print(f"  median min_all/tail among dead {np.median(R['min_all_exact'][dead] / R['tail'][dead]):.3f}, "
      f"d_v/tail among dead {np.median(R['d_v'][dead] / R['tail'][dead]):.3f}")
