#!/usr/bin/env python3
"""Is ~/projects/data/sift100m_A1.2_server/ the index that produced the server traces/explogs?

Checks (explog = server-recorded expansions: parent node, neighbour id, host PQ distance d):
  1 graph     for every expanded parent in the explog, the recorded neighbour ids must be in the parent's
              neighbour list of disk.index. Negative control: the local pq0 build (different graph).
  2 vectors   the 128-B vector stored in each disk.index node equals base.100M.u8bin[id].
  3 PQ        DiskANN PQ distance computed from pq_pivots.bin + pq_compressed.bin for (query, neighbour) equals
              the explog's d (float32 round-off only).
  4 header    medoid in disk.index == start_node of every explog query.
Usage: ssdpq/verify_server_index.py [--queries 50]
"""
import argparse, collections, csv
from pathlib import Path
import numpy as np
import coarse_quant as cq
import sys as _sys, pathlib as _pl; _sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1] / 'common'))
from explog import REC

SRV = Path('/home/emmanuel/projects/data/sift100m_A1.2_server/disk_index_SIFT100M_base100M_R64_L100_A1.2')
LOC = '/home/emmanuel/projects/data/sift100m_diskann_indexes/sift100m_l2_R64_L100_pq0_default_disk.index'
EXP = Path('results/diskann_policy_sweep_20261115T184338Z_C0/explog_W4')
NODE, NPS, SECTOR = 388, 10, 4096


class DiskIndex:
    def __init__(self, path):
        self.mm = np.memmap(path, dtype=np.uint8, mode='r')
        h = np.frombuffer(self.mm[:72], dtype=np.uint64)
        self.n, self.dim, self.medoid, self.node, self.nps = int(h[1]), int(h[2]), int(h[3]), int(h[4]), int(h[5])

    def node_at(self, i):
        off = SECTOR * (1 + i // NPS) + NODE * (i % NPS)
        b = self.mm[off:off + NODE]
        nn = int(b[128:132].view(np.uint32)[0])
        return b[:128], b[132:132 + 4 * nn].view(np.uint32)


def load_pq(prefix):
    """DiskANN PQ: pivots file = metadata bin (offsets) + full pivots (256 x dim f32) + centroid (dim f32)
    + chunk offsets (u32); compressed = int32 n, int32 nchunks, then n x nchunks u8."""
    f = open(f'{prefix}_pq_pivots.bin', 'rb').read()
    def binat(off, dt):
        n, d = np.frombuffer(f, dtype=np.int32, count=2, offset=off)
        return np.frombuffer(f, dtype=dt, count=n * d, offset=off + 8).reshape(n, d)
    offs = binat(0, np.uint64).ravel()
    piv = binat(int(offs[0]), np.float32); cen = binat(int(offs[1]), np.float32).ravel()
    chunk = binat(int(offs[2]), np.uint32).ravel()
    c = np.memmap(f'{prefix}_pq_compressed.bin', dtype=np.uint8, mode='r')
    n, m = np.frombuffer(c[:8].tobytes(), dtype=np.int32)
    codes = c[8:].reshape(n, m)
    return piv, cen, chunk, codes


def pq_dist(q, ids, piv, cen, chunk, codes):
    qc = q.astype(np.float32) - cen
    pc = piv  # pivots are stored in the centred space (k-means ran on data - centroid)
    tab = np.stack([((qc[chunk[j]:chunk[j + 1]] - pc[:, chunk[j]:chunk[j + 1]]) ** 2).sum(1)
                    for j in range(len(chunk) - 1)])  # nchunks x 256
    cd = np.asarray(codes[np.sort(ids)])[np.argsort(np.argsort(ids))]
    return tab[np.arange(len(chunk) - 1)[None, :], cd].sum(1)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--queries', type=int, default=50); a = ap.parse_args()
    qrows = list(csv.DictReader(open(EXP / 'expansions.bin.queries.csv')))[:a.queries]
    nrec = sum(int(r['records']) for r in qrows)
    rec = np.fromfile(EXP / 'expansions.bin', dtype=REC, count=nrec)
    print(f'explog: {EXP}  {len(qrows)} queries, {nrec} records')
    srv, loc = DiskIndex(f'{SRV}_disk.index'), DiskIndex(LOC)
    for nm, g in (('server', srv), ('local pq0', loc)):
        print(f'[4 header] {nm}: n={g.n} dim={g.dim} medoid={g.medoid} node={g.node}B nodes/sector={g.nps}')
    starts = {int(r['start_node']) for r in qrows}
    print(f'[4 header] explog start_node values: {sorted(starts)}  -> server medoid match: {starts == {srv.medoid}}, '
          f'local match: {starts == {loc.medoid}}')

    by_parent = collections.defaultdict(set)
    for t in rec: by_parent[int(t['parent'])].add(int(t['nbr']))
    for nm, g in (('server', srv), ('local pq0', loc)):
        ok = tot = full = 0
        for p, nb in by_parent.items():
            _, lst = g.node_at(p); s = set(lst.tolist())
            hit = len(nb & s); ok += hit; tot += len(nb); full += hit == len(nb)
        print(f'[1 graph]  {nm}: recorded neighbours found in disk.index lists {ok}/{tot} ({ok / tot:.4%}); '
              f'parents fully matching {full}/{len(by_parent)}')

    base = cq.load_base(); Q = cq.load_query()
    ps = np.array(sorted(by_parent))[:2000]
    eq = sum(np.array_equal(np.asarray(srv.node_at(int(p))[0]), np.asarray(base[int(p)])) for p in ps)
    print(f'[2 vectors] server disk.index vector == base.100M.u8bin for {eq}/{len(ps)} nodes')

    piv, cen, chunk, codes = load_pq(SRV)
    print(f'[3 PQ] pivots {piv.shape}, centroid {cen.shape}, chunks {len(chunk) - 1} (bytes/vector), codes {codes.shape}')
    rel = []; pos = 0
    for r in qrows:
        n = int(r['records']); x = rec[pos:pos + n]; pos += n
        q = int(r['query_id'])
        d = pq_dist(Q[q], x['nbr'].astype(np.int64), piv, cen, chunk, codes)
        rel.append(np.abs(d - x['d']) / np.maximum(np.abs(x['d']), 1e-6))
    rel = np.concatenate(rel)
    print(f'[3 PQ] |computed - explog d| / d over {len(rel)} records: median {np.median(rel):.2e}, '
          f'p99 {np.quantile(rel, .99):.2e}, max {rel.max():.2e}, exact-to-1e-4 {np.mean(rel < 1e-4):.4%}')


if __name__ == '__main__':
    main()
