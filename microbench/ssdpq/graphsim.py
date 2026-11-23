#!/usr/bin/env python3
"""Functional DiskANN beam-search simulator on a local disk index (no timing; counts rounds, reads, sensed units).

Graph: a DiskANN _disk.index (uint8, R64, 10 nodes per 4 KiB sector, node = 128 B vector + u32 n + 64 u32 ids).
Navigation distance: a quantizer from coarse_quant (host side, default PQ32x8 ~ the server's 31-byte PQ), encoded
on demand. Result: the expanded nodes re-ranked by exact distance (as DiskANN does), recall@K against GT.

Layout (where a node lives physically): 'default' (node id order) or 'shuffle' (block-shuffle old_to_new map of the
same graph). Sensing unit: one flash page = PAGE_NODES consecutive slots (40 = 16 KiB page of 4 sectors).

Search variants:
  base            DiskANN: each round reads the W nearest unexpanded candidates.
  coexp=<unit>    co-located expansion: when a node is read, the other nodes in the same unit (sector = 10 slots or
                  page = 40 slots) are expanded as well at no extra tR (SSD-side: the page is already sensed).
                  coexp_filter: only co-located nodes whose host distance beats the current L-th candidate.
  late_far=<x>    late decision inside a round (SSD side, when a read would start): each read not yet started is
                  re-checked against x * the K-th candidate updated by the reads already completed (upper bound).
  skip_far=<x>    host distance termination: a taken candidate with host distance > x * K-th candidate is not read
                  (and not re-ranked); combinable with coexp.
  ssd=<k>,<spec>  first k rounds run inside the SSD with a coarse code <spec> (navigation by coarse distance only),
                  then the host continues with its own distances (its list rebuilt from everything the SSD expanded).
Metrics per query: rounds (host-visible + SSD rounds), reads (nodes requested), sensed units (distinct pages: tR),
expanded nodes, recall@K.
"""
import argparse, bisect, json, os, time
from pathlib import Path
import numpy as np
import coarse_quant as cq

IDX = '/home/emmanuel/projects/data/sift100m_diskann_indexes/sift100m_l2_R64_L100_pq0_default_disk.index'
O2N = '/home/emmanuel/projects/data/sift100m_diskann_indexes/sift100m_l2_R64_L100_pq0_block_shuffle_disk.index_block_shuffle_old_to_new.bin'
GT = '/home/emmanuel/projects/data/sift100m/idx_100M.bin'
NODE, NPS, SECTOR = 388, 10, 4096
# GRAPHSIM_INDEX=server: the server A1.2 index (the one behind all server traces; vanilla layout) and its real 31-byte
# host PQ ('server' code). validate_graphsim_server.py shows this search reproduces the server run read for read.
SRV_PREFIX = '/home/emmanuel/projects/data/sift100m_A1.2_server/disk_index_SIFT100M_base100M_R64_L100_A1.2'
SERVER = os.environ.get('GRAPHSIM_INDEX', '') == 'server'
if SERVER: IDX = SRV_PREFIX + '_disk.index'


class Graph:
    def __init__(self, path=IDX):
        h = np.fromfile(path, dtype=np.uint64, count=6)
        self.n, self.medoid = int(h[1]), int(h[3])
        self.mm = np.memmap(path, dtype=np.uint8, mode='r')

    def node(self, i):
        off = SECTOR * (1 + i // NPS) + NODE * (i % NPS)
        b = self.mm[off:off + NODE]
        vec = b[:128].astype(np.float32)
        nn = int(b[128:132].view(np.uint32)[0])
        return vec, b[132:132 + 4 * nn].view(np.uint32).astype(np.int64)


class Codes:
    """Per-query on-demand encoding of a trained quantizer (PQ or RBQ); id -> distance to the current query."""

    def __init__(self, qz, base):
        self.qz, self.base = qz, base

    def dists(self, q, ids):
        x = np.asarray(self.base[ids], dtype=np.float32)
        self.qz.encode(x)
        return self.qz.dist(q, np.arange(len(ids)))


class ServerCodes:
    """The server's own DiskANN PQ (pq_pivots + pq_compressed, 31 B per point); same interface as Codes."""

    def __init__(self, prefix=SRV_PREFIX):
        import verify_server_index as vs
        self.piv, self.cen, self.chunk, self.codes = vs.load_pq(prefix)
        self.m = len(self.chunk) - 1; self._key = None

    def dists(self, q, ids):
        k = q.tobytes()
        if k != self._key:
            qc = q.astype(np.float32) - self.cen
            self._tab = np.stack([((qc[self.chunk[j]:self.chunk[j + 1]] - self.piv[:, self.chunk[j]:self.chunk[j + 1]]) ** 2)
                                  .sum(1) for j in range(self.m)]); self._key = k
        cd = np.asarray(self.codes[np.asarray(ids, dtype=np.int64)])
        return self._tab[np.arange(self.m)[None, :], cd].sum(1)


class CL:
    def __init__(self, L):
        self.L, self.k, self.e = L, [], []

    def insert(self, d, i):
        key = (d, i)
        if len(self.k) == self.L and self.k[-1] < key: return False
        p = bisect.bisect_left(self.k, key)
        self.k.insert(p, key); self.e.insert(p, False)
        if len(self.k) > self.L: self.k.pop(); self.e.pop()
        return True

    def take(self, W):
        out = []
        for j, e in enumerate(self.e):
            if not e:
                self.e[j] = True; out.append(self.k[j][1])
                if len(out) == W: break
        return out

    def tail(self):
        return self.k[-1][0] if len(self.k) == self.L else np.inf


def search(g, base, q, host, W, L, unit_of, coexp=None, coexp_filter=False, ssd=None, page_nodes=40, slot=None,
           filt=None, sector_of=None, filter_k=0, skip_far=0.0, K=10, late=None):
    """Returns dict of counters and the expanded set with exact distances."""
    visited = {g.medoid}; expanded = {}
    cl = CL(L)
    cl.insert(float(host.dists(q, np.array([g.medoid]))[0]), g.medoid)
    rounds = ssd_rounds = reads = skipped = late_skipped = 0; sensed = set(); sched = []

    def expand(i, cl, dist_codes):
        vec, nb = g.node(i)
        expanded[i] = float(((vec - q) ** 2).sum())
        nb = np.array([j for j in nb if j not in visited], dtype=np.int64)
        if len(nb):
            visited.update(nb.tolist())
            for d, j in zip(dist_codes.dists(q, nb), nb): cl.insert(float(d), int(j))

    # phase 1 (optional): SSD-side rounds with a coarse code
    if ssd:
        k, coarse = ssd
        scl = CL(L); scl.insert(float(coarse.dists(q, np.array([g.medoid]))[0]), g.medoid)
        for _ in range(k):
            F = scl.take(W)
            if not F: break
            ssd_rounds += 1; reads += len(F)
            for i in F:
                sensed.add(unit_of(i)); expand(i, scl, coarse)
        # hand over: the host re-scores every visited-but-unexpanded node with its own code
        cand = np.array([i for i in visited if i not in expanded], dtype=np.int64)
        cl = CL(L)
        if len(cand):
            for d, j in zip(host.dists(q, cand), cand): cl.insert(float(d), int(j))
    while True:
        F = [i for i in cl.take(W) if i not in expanded]
        if not F:
            if all(cl.e): break
            continue
        if skip_far:
            # host distance termination (= analyze_avoidable_reads far:x, Adaptive Beam Search): a taken candidate
            # whose host distance exceeds x * the K-th candidate (both at round start) is dropped without a read
            kth = cl.k[min(K, len(cl.k)) - 1][0]
            dF = {jj: dd for dd, jj in cl.k if jj in F}
            n = len(F); F = [i for i in F if dF[i] <= skip_far * kth]; skipped += n - len(F)
            if not F: continue
        rounds += 1; n0 = len(expanded)

        def read_one(i):
            u = unit_of(i); sensed.add(u)
            expand(i, cl, host)
            if coexp and filt is not None:
                # SSD-side filter: score the co-located nodes with code `filt`, send those beating the current tail
                cand = np.array([j for j in coexp(i) if j != i and j not in expanded], dtype=np.int64)
                if len(cand):
                    tail = cl.tail() if not filter_k else (cl.k[min(filter_k, len(cl.k)) - 1][0] if cl.k else np.inf)
                    for j in cand[filt.dists(q, cand) < tail]:
                        j = int(j)
                        if j in expanded: continue
                        visited.add(j); expand(j, cl, host)
                        for t, (d, jj) in enumerate(cl.k):
                            if jj == j: cl.e[t] = True; break
            elif coexp:
                for j in coexp(i):
                    if j == i or j in expanded: continue
                    if coexp_filter:
                        dj = float(host.dists(q, np.array([j]))[0])
                        if dj >= cl.tail(): continue
                    visited.add(j); expand(j, cl, host)
                    # mark j expanded in the list if it is there
                    for t, (d, jj) in enumerate(cl.k):
                        if jj == j: cl.e[t] = True; break

        if late and len(F) > late[1]:
            # late decision (SSD-side, at the moment a read would start on its die): reads of the round complete one
            # by one; at most `commit` are in flight, and each further read is decided only when one in-flight read
            # has completed, with the K-th candidate as updated by every completed read (host-quality distances:
            # an information upper bound). A candidate beyond x * that K-th is dropped without a read.
            x, commit, order, lrng = late
            dF = {jj: dd for dd, jj in cl.k if jj in F}
            seq = sorted(F, key=dF.get) if order == 'near' else [F[t] for t in lrng.permutation(len(F))]
            started, ptr = [], 0
            for i in seq:
                if len(started) >= commit:
                    while ptr < len(started) - commit + 1: read_one(started[ptr]); ptr += 1
                    if dF[i] > x * cl.k[min(K, len(cl.k)) - 1][0]: late_skipped += 1; continue
                started.append(i)
            while ptr < len(started): read_one(started[ptr]); ptr += 1
            F = started
        else:
            for i in F: read_one(i)
        reads += len(F)
        if sector_of: sched.append({'F': [sector_of(i) for i in F], 'exp': len(expanded) - n0})
    return dict(rounds=rounds, ssd_rounds=ssd_rounds, reads=reads, skipped=skipped, late_skipped=late_skipped, sensed=len(sensed), expanded=len(expanded),
                result=sorted(expanded, key=expanded.get), sched=sched)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--W', type=int, default=4)
    ap.add_argument('--L', type=int, default=100)
    ap.add_argument('--K', type=int, default=10)
    ap.add_argument('--queries', type=int, default=500)
    ap.add_argument('--q0', type=int, default=0)
    ap.add_argument('--layout', choices=['default', 'shuffle', 'custom'], default='default')
    ap.add_argument('--o2n', type=Path, default=None, help='custom layout: old_to_new slot map (header n,1)')
    ap.add_argument('--coexp', choices=['none', 'sector', 'page'], default='none')
    ap.add_argument('--coexp_filter', action='store_true')
    ap.add_argument('--host', default='server' if SERVER else 'PQ32x8', help="'server' = the server's 31-B DiskANN PQ")
    ap.add_argument('--ssd_rounds', type=int, default=0)
    ap.add_argument('--ssd_spec', default='PQ8x8')
    ap.add_argument('--cbdir', type=Path, default=Path('results/autonomous_20261122/codebooks'))
    ap.add_argument('--filter_spec', default='', help='SSD-side filter code for co-located nodes (batch, vs tail)')
    ap.add_argument('--filter_k', type=int, default=0, help='filter threshold = k-th candidate (0 = L-th, the tail)')
    ap.add_argument('--skip_far', type=float, default=0.0, help='>0: host drops a taken candidate whose host distance > x * K-th candidate (no read)')
    ap.add_argument('--late_far', type=float, default=0.0, help='>0: late (SSD-side) decision within a round, see search()')
    ap.add_argument('--late_commit', type=int, default=1, help='late decision: reads in flight at once (1 = fully serialized upper bound)')
    ap.add_argument('--late_order', choices=['random', 'near'], default='random', help='late decision: completion order within a round')
    ap.add_argument('--dump_sched', type=Path, default=None, help='write a ladder_sim schedule (sector = slot // 10)')
    ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args()
    g = Graph(); base = cq.load_base(); Q = cq.load_query()
    gt = np.fromfile(GT, dtype=np.uint32)
    ngt, kgt = int(gt[0]), int(gt[1]); gt = gt[2:2 + ngt * kgt].reshape(ngt, kgt)

    def trained(spec):
        a.cbdir.mkdir(parents=True, exist_ok=True)
        f = a.cbdir / f'{spec}.npz'
        qz = cq.make(spec)
        if f.exists() and spec != 'exact':
            z = np.load(f)
            for k in z.files: setattr(qz, k, z[k])
            if hasattr(qz, 'cb'): qz.ds = qz.cb.shape[2]
            return qz
        T = cq.gather(base, np.sort(np.random.default_rng(1).choice(base.shape[0], 200000, replace=False)))
        qz.train(T)
        if hasattr(qz, 'cb'): np.savez(f, cb=qz.cb)
        elif hasattr(qz, 'P'): np.savez(f, c=qz.c, P=qz.P)
        return qz

    codes = lambda spec: ServerCodes() if spec == 'server' else Codes(trained(spec), base)
    host = codes(a.host)
    ssd = (a.ssd_rounds, codes(a.ssd_spec)) if a.ssd_rounds else None
    if a.layout == 'default':
        slot = lambda i: i
    elif a.layout == 'custom':
        o2n_c = np.fromfile(a.o2n, dtype=np.uint32, offset=8)
        n2o_c = np.full(int(o2n_c.max()) + 1, 0xffffffff, dtype=np.uint32); n2o_c[o2n_c] = np.arange(len(o2n_c), dtype=np.uint32)
        slot = lambda i: int(o2n_c[i])
    else:
        assert not SERVER, 'the block-shuffle map belongs to the local pq0 graph; use --layout custom --o2n for the server'
        o2n = np.memmap(O2N, dtype=np.uint32, mode='r', offset=8)
        n2o = None
        slot = lambda i: int(o2n[i])
    PAGE = 40
    unit_of = lambda i: slot(i) // PAGE
    coexp = None
    if a.coexp != 'none':
        U = NPS if a.coexp == 'sector' else PAGE
        if a.layout == 'default':
            coexp = lambda i: range((i // U) * U, (i // U) * U + U)
        elif a.layout == 'custom':
            coexp = lambda i: [int(n2o_c[s]) for s in range((slot(i) // U) * U, min((slot(i) // U) * U + U, len(n2o_c)))
                               if n2o_c[s] != 0xffffffff]
        else:
            n2o = np.memmap(O2N.replace('old_to_new', 'new_to_old'), dtype=np.uint32, mode='r', offset=8)
            coexp = lambda i: [int(n2o[s]) for s in range((slot(i) // U) * U, (slot(i) // U) * U + U) if s < g.n]
    filt = codes(a.filter_spec) if a.filter_spec else None
    sector_of = (lambda i: slot(i) // NPS) if a.dump_sched else None
    late = (a.late_far, a.late_commit, a.late_order, np.random.default_rng(7)) if a.late_far else None
    rows = []; scheds = []; t0 = time.time()
    for qi in range(a.q0, a.q0 + a.queries):
        r = search(g, base, Q[qi], host, a.W, a.L, unit_of, coexp, a.coexp_filter, ssd, filt=filt, sector_of=sector_of,
                   filter_k=a.filter_k, skip_far=a.skip_far, K=a.K, late=late)
        res = r.pop('result'); scheds.append(r.pop('sched'))
        r['recall'] = len(set(res[:a.K]) & set(gt[qi, :a.K].tolist())) / a.K
        rows.append(r)
    m = {k: float(np.mean([r[k] for r in rows])) for k in rows[0]}
    out = dict(vars(a), **{'mean': m, 'seconds': time.time() - t0})
    out = {k: (str(v) if isinstance(v, Path) else v) for k, v in out.items()}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out))
    if a.dump_sched:
        import pickle
        pickle.dump({'W': a.W, 'L': a.L, 'queries': scheds, 'source': str(a.out)}, open(a.dump_sched, 'wb'))
    print(json.dumps(out['mean']), f"{out['seconds']:.0f}s", flush=True)


if __name__ == '__main__':
    main()
