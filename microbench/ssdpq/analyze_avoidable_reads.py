#!/usr/bin/env python3
"""Which DiskANN reads could be skipped altogether (not just made cheaper)? Hindsight classification of every read.

Runs the plain DiskANN beam search of ssdpq/graphsim.py (same graph, host code, W, L) and labels each read v:
  result      v is in the final top-K (its full vector is needed for the re-rank)
  path        v is an ancestor (discoverer chain) of a final top-K node: needed to reach the result
  productive  some neighbour v inserted into the list was later read (but not on a result path)
  inserted    v inserted neighbours, none of them was ever read
  dead        v inserted nothing (all neighbours visited or worse than the L-th candidate at that moment)
Also: 'late' = read after the round in which the final top-K set was last changed (convergence tail).

Skip experiments (the read is not issued; v keeps its host distance in the list but is not re-ranked):
  --skip dead_oracle   skip v if, by peeking v's neighbour list for free, it would insert nothing (upper bound of
                       a "neighbourhood summary" predictor kept in SSD DRAM)
  --skip dead_far:<x>  dead_oracle, but only when v's host distance > x * K-th candidate (keep likely results)
  --skip far:<x>       skip v if its host distance > x * current K-th candidate distance (cheap, list-only rule)
  --skip nbrvis:<m>[:<x>]  SSD-side rule: the SSD sees every neighbour list it returns, so it can keep the visited set
                       itself; with the m nearest neighbours of each node (ids, 4m B per node, precomputed offline)
                       it skips v when all m are already visited [and v's host distance > x * K-th candidate]
Usage: ssdpq/analyze_avoidable_reads.py --out f.json [--W 4 --L 100 --queries 300 --skip none]
"""
import argparse, json, time
from pathlib import Path
import numpy as np
import coarse_quant as cq
import graphsim as gs


def search(g, q, host, W, L, K, skip='none', base=None, near=None):
    visited = {g.medoid}; expanded = {}; parent = {g.medoid: -1}
    cl = gs.CL(L); cl.insert(float(host.dists(q, np.array([g.medoid]))[0]), g.medoid)
    reads = []  # per read: dict(v, round, ins, ins_ids)
    skipped = 0; skip_dead = 0; rnd = 0; topk_hist = []
    hostd = {}
    while True:
        F = [i for i in cl.take(W) if i not in expanded]
        if not F:
            if all(cl.e): break
            continue
        rnd += 1
        dF = {jj: dd for dd, jj in cl.k if jj in F}
        kth = cl.k[min(K, len(cl.k)) - 1][0]
        for i in F:
            vec, nb = g.node(i)
            nbn = np.array([j for j in nb if j not in visited], dtype=np.int64)
            d = host.dists(q, nbn) if len(nbn) else np.zeros(0)
            dead = not (len(d) and (d < cl.tail()).any())
            if skip == 'dead_oracle' and dead:
                skipped += 1; continue
            if skip.startswith('dead_far:') and dead and dF[i] > float(skip[9:]) * kth:
                skipped += 1; continue
            if skip.startswith('far:') and dF[i] > float(skip[4:]) * kth:
                skipped += 1; skip_dead += dead; continue
            if skip.startswith('nbrvis:'):
                parts = skip.split(':'); m = int(parts[1]); x = float(parts[2]) if len(parts) > 2 else 0.0
                if i not in near:
                    X = np.asarray(base[np.sort(nb)], dtype=np.float32)[np.argsort(np.argsort(nb))]
                    near[i] = nb[np.argsort(((X - vec) ** 2).sum(1), kind='stable')]
                if all(int(j) in visited for j in near[i][:m]) and dF[i] > x * kth:
                    skipped += 1; skip_dead += dead; continue
            expanded[i] = float(((vec - q) ** 2).sum())
            ins = []
            if len(nbn):
                visited.update(nbn.tolist())
                for dd, j in zip(d, nbn):
                    if cl.insert(float(dd), int(j)): ins.append(int(j)); parent.setdefault(int(j), i)
            reads.append(dict(v=i, round=rnd, ins=ins))
        topk_hist.append(tuple(sorted(expanded, key=expanded.get)[:K]))
    res = sorted(expanded, key=expanded.get)[:K]
    # classify
    path = set()
    for r in res:
        p = parent.get(r, -1)
        while p != -1 and p not in path: path.add(p); p = parent.get(p, -1)
    last_change = next((t + 1 for t in range(len(topk_hist) - 1, 0, -1) if topk_hist[t] != topk_hist[t - 1]), 1)
    resset = set(res); lab = {}
    for r in reads:
        v = r['v']
        if v in resset: c = 'result'
        elif v in path: c = 'path'
        elif any(j in expanded for j in r['ins']): c = 'productive'
        elif r['ins']: c = 'inserted'
        else: c = 'dead'
        lab[c] = lab.get(c, 0) + 1
    late = sum(1 for r in reads if r['round'] > last_change)
    late_dead = sum(1 for r in reads if r['round'] > last_change and not r['ins'])
    return dict(reads=len(reads), skipped=skipped, skipped_dead=skip_dead, rounds=rnd, last_change_round=last_change, late=late,
                late_dead=late_dead, **{f'n_{k}': lab.get(k, 0) for k in ['result', 'path', 'productive', 'inserted', 'dead']},
                result=res)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--W', type=int, default=4); ap.add_argument('--L', type=int, default=100)
    ap.add_argument('--K', type=int, default=10); ap.add_argument('--queries', type=int, default=300)
    ap.add_argument('--host', default='PQ32x8'); ap.add_argument('--skip', default='none')
    ap.add_argument('--cbdir', type=Path, default=Path('results/autonomous_20261122/codebooks'))
    ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args()
    g = gs.Graph(); base = cq.load_base(); Q = cq.load_query()
    gt = np.fromfile(gs.GT, dtype=np.uint32); ngt, kgt = int(gt[0]), int(gt[1]); gt = gt[2:2 + ngt * kgt].reshape(ngt, kgt)
    qz = cq.make(a.host); z = np.load(a.cbdir / f'{a.host}.npz')
    for k in z.files: setattr(qz, k, z[k])
    qz.ds = qz.cb.shape[2]
    host = gs.ServerCodes() if gs.SERVER else gs.Codes(qz, base)
    rows = []; t0 = time.time(); near = {}
    for qi in range(a.queries):
        r = search(g, Q[qi], host, a.W, a.L, a.K, a.skip, base, near)
        res = r.pop('result'); r['recall'] = len(set(res) & set(gt[qi, :a.K].tolist())) / a.K
        rows.append(r)
    m = {k: float(np.mean([r[k] for r in rows])) for k in rows[0]}
    out = dict(vars(a), mean=m, seconds=time.time() - t0)
    out = {k: (str(v) if isinstance(v, Path) else v) for k, v in out.items()}
    a.out.parent.mkdir(parents=True, exist_ok=True); a.out.write_text(json.dumps(out))
    print(a.skip, json.dumps({k: round(v, 3) for k, v in m.items()}), f"{out['seconds']:.0f}s", flush=True)


if __name__ == '__main__':
    main()
