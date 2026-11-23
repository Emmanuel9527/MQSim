#!/usr/bin/env python3
"""Does a Python re-implementation of DiskANN's beam search on the server index reproduce the real server run?

Index: ~/projects/data/sift100m_A1.2_server (vanilla layout, 31-byte host PQ from pq_pivots + pq_compressed).
Search: the host candidate list is common/explog.CandList (replica of diskann::NeighborPriorityQueue); every round
takes the W nearest unexpanded candidates, reads them, computes the exact L2 of each read node (re-rank list) and
inserts its not-yet-visited neighbours with their PQ distance. Result: top-K of the read nodes by exact distance.
Reference: the server run of diskann_policy_sweep.sh (explog_W<W>): trace_access.csv (node ids read in every
round) and res_<L>_idx_uint32.bin (returned ids).
Prints: queries whose whole read sequence matches, rounds matching, and result agreement.
Usage: validate_graphsim_server.py [--W 4] [--queries 100]
"""
import argparse, collections, csv
from pathlib import Path
import numpy as np
import coarse_quant as cq
import verify_server_index as vs
import sys as _sys, pathlib as _pl; _sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1] / 'common'))
from explog import CandList  # noqa: E402

EXP = Path(__file__).resolve().parents[1] / 'results/diskann_policy_sweep_20261115T184338Z_C0'


class ServerPQ:
    def __init__(self):
        self.piv, self.cen, self.chunk, self.codes = vs.load_pq(vs.SRV)
        self.m = len(self.chunk) - 1

    def table(self, q):
        qc = q.astype(np.float32) - self.cen
        return np.stack([((qc[self.chunk[j]:self.chunk[j + 1]] - self.piv[:, self.chunk[j]:self.chunk[j + 1]]) ** 2)
                         .sum(1) for j in range(self.m)])

    def dists(self, tab, ids):
        cd = np.asarray(self.codes[np.asarray(ids, dtype=np.int64)])
        return tab[np.arange(self.m)[None, :], cd].sum(1)


def search(g, pq, q, W, L, K):
    tab = pq.table(q)
    cl = CandList(L); visited = {g.medoid}
    cl.insert(float(pq.dists(tab, [g.medoid])[0]), g.medoid)
    exact, rounds = {}, []
    while True:
        F = cl.take(W)
        if not F: break
        rounds.append(F)
        for i in F:
            vec, nb = g.node_at(i)
            exact[i] = float(((np.asarray(vec, dtype=np.float32) - q) ** 2).sum())
            new = [int(j) for j in nb if int(j) not in visited]
            if new:
                visited.update(new)
                for d, j in zip(pq.dists(tab, new), new): cl.insert(float(d), j)
    return rounds, sorted(exact, key=lambda i: (exact[i], i))[:K]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--W', type=int, default=4); ap.add_argument('--L', type=int, default=100)
    ap.add_argument('--K', type=int, default=10); ap.add_argument('--queries', type=int, default=100)
    a = ap.parse_args()
    d = EXP / f'explog_W{a.W}'
    acc = collections.defaultdict(lambda: collections.defaultdict(list))
    with open(d / 'trace_access.csv') as f:
        for r in csv.DictReader(f):
            acc[int(r['query_id'])][int(r['iteration'])].append(int(r['node_id']))
    res = np.fromfile(d / f'res_{a.L}_idx_uint32.bin', dtype=np.uint32)
    nq, k = int(res[0]), int(res[1]); res = res[2:2 + nq * k].reshape(nq, k)
    g = vs.DiskIndex(f'{vs.SRV}_disk.index'); pq = ServerPQ(); Q = cq.load_query()
    full = rmatch = rtot = resmatch = 0; first_bad = []
    for qi in range(a.queries):
        rounds, top = search(g, pq, Q[qi], a.W, a.L, a.K)
        real = [acc[qi][t] for t in sorted(acc[qi])]
        ok = [x == y for x, y in zip(rounds, real)]
        rmatch += sum(ok); rtot += max(len(rounds), len(real))
        if all(ok) and len(rounds) == len(real): full += 1
        elif len(first_bad) < 3:
            t = ok.index(False) if False in ok else min(len(rounds), len(real))
            first_bad.append((qi, t, rounds[t] if t < len(rounds) else None, real[t] if t < len(real) else None))
        resmatch += list(top) == [int(x) for x in res[qi, :a.K]]
    print(f'W{a.W} L{a.L}, {a.queries} queries vs server explog_W{a.W}:')
    print(f'  whole read sequence identical: {full}/{a.queries}')
    print(f'  rounds identical: {rmatch}/{rtot} ({rmatch / rtot:.4%})')
    print(f'  top-{a.K} result identical (ids and order): {resmatch}/{a.queries}')
    for b in first_bad: print(f'  first mismatch: query {b[0]} round {b[1]}: sim {b[2]} vs real {b[3]}')


if __name__ == '__main__':
    main()
