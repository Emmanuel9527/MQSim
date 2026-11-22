#!/usr/bin/env python3
"""Which reads need an SSD-side PQ, how many survive the host node cache, and how concentrated are they?

A read of round r+1 is "new" when its node was first seen as a neighbour in round r (the host could not hint it;
only an SSD with distance estimates can predict it), else "old" (host hint covers it). Classes come from replaying
the host list of an explog (1000 queries, no cache). The cached trace (same queries, same search path; cached nodes
are expanded on the host without I/O) tells which reads still reach the SSD.

Outputs (JSON):
  per round index r: reads, new reads, SSD-visible reads, SSD-visible new reads (no cache / with cache)
  PQ lookups an SSD would need to predict the SSD-visible new reads (= new neighbours of round r when round r+1
  has a visible new read), per query and distinct over queries, with and without cache
  hot-set coverage (10k-query traces): rank nodes by early-phase SSD-read frequency on queries 5000-9999,
  coverage of early-phase SSD reads of queries 0-4999 by the top-N nodes (early = rounds in the first third)
Usage: ssdpq/analyze_pq_footprint.py --explog <dir> --W 4 --trace <uncached trace dir> --ctrace <cached trace dir> --out x.json
"""
import argparse, collections, csv, json
from pathlib import Path
import numpy as np
import sys as _sys, pathlib as _pl; _sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1] / 'common'))
from explog import REC, CandList


def read_trace(path, qmax=None):
    """{q: {it: [node,...]}} from trace_access.csv (query_id col 6, iteration 7, node 9)."""
    t = collections.defaultdict(lambda: collections.defaultdict(list))
    with open(path) as f:
        next(f)
        for line in f:
            c = line.split(',')
            q = int(c[6])
            if qmax is not None and q >= qmax: continue
            t[q][int(c[7])].append(int(c[9]))
    return t


def read_stats_iters(path):
    """{q: total_iterations} from trace_stats.csv."""
    out = {}
    with open(path) as f:
        h = next(f).rstrip('\n').split(',')
        iq, it = h.index('query_id'), h.index('total_iterations')
        for line in f:
            c = line.split(',')
            out[int(c[iq])] = int(c[it])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--explog', type=Path, required=True)
    ap.add_argument('--W', type=int, required=True)
    ap.add_argument('--L', type=int, default=100)
    ap.add_argument('--trace', type=Path, required=True)
    ap.add_argument('--ctrace', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args()
    W, L = a.W, a.L
    qrows = list(csv.DictReader(open(a.explog / 'expansions.bin.queries.csv')))
    rec = np.fromfile(a.explog / 'expansions.bin', dtype=REC, count=sum(int(r['records']) for r in qrows))
    tr = read_trace(a.trace / 'trace_access.csv')
    ctr = read_trace(a.ctrace / 'trace_access.csv')
    iters = read_stats_iters(a.trace / 'trace_stats.csv')
    citers = read_stats_iters(a.ctrace / 'trace_stats.csv')
    same = sum(iters[q] == citers.get(q) for q in iters)
    print(f'queries with equal rounds uncached/cached: {same}/{len(iters)}', flush=True)

    # ---- per-round classes on explog queries
    RMAX = 64
    prof = np.zeros((RMAX, 6))   # reads, new, vis_nc(=reads), vis_c, new_vis_c, rounds
    lookups = {'nocache': [], 'cache': []}; uni = {'nocache': set(), 'cache': set()}
    uni_curve = {'nocache': [], 'cache': []}
    pos = 0; mism = 0
    for r in qrows:
        q, n = int(r['query_id']), int(r['records'])
        x = rec[pos:pos + n]; pos += n
        by_it = collections.defaultdict(list)
        for t in x:
            if t['f'] & 1: by_it[int(t['it'])].append(t)
        cl = CandList(L); cl.insert(float(r['start_pq_distance']), int(r['start_node']))
        F, newsets = [], []
        for it in range(int(r['iterations'])):
            f = cl.take(W)
            if f != tr[q].get(it, []): mism += 1
            F.append(f)
            ns = set()
            for t in by_it.get(it, []):
                cl.insert(float(t['d']), int(t['nbr'])); ns.add(int(t['nbr']))
            newsets.append(ns)
        cq = ctr.get(q, {})
        lk = {'nocache': 0, 'cache': 0}
        for it in range(1, len(F)):
            tgt = F[it]
            newt = [i for i in tgt if i in newsets[it - 1]]
            vis = set(cq.get(it, []))
            newvis = [i for i in newt if i in vis]
            ri = min(it, RMAX - 1)
            prof[ri] += [len(tgt), len(newt), len(tgt), len(vis), len(newvis), 1]
            # an SSD predicting round it's new reads must rank all new neighbours of round it-1
            if newt:
                lk['nocache'] += len(newsets[it - 1]); uni['nocache'] |= newsets[it - 1]
            if newvis:
                lk['cache'] += len(newsets[it - 1]); uni['cache'] |= newsets[it - 1]
        for k in lk: lookups[k].append(lk[k])
        for k in uni_curve: uni_curve[k].append(len(uni[k]))
    print(f'explog replay vs uncached trace mismatches: {mism}', flush=True)
    tot = prof.sum(0)
    print(f'reads {tot[0]:.0f}  new {tot[1] / tot[0]:.1%}  SSD-visible with cache {tot[3] / tot[0]:.1%}  '
          f'new&visible with cache {tot[4] / tot[0]:.1%} (= {tot[4] / max(tot[1], 1):.1%} of new)', flush=True)

    # ---- hot-set coverage on 10k queries, early third of each query's rounds
    def early_reads(t, it_total, lo, hi):
        cnt = collections.Counter(); tot = 0
        for q in range(lo, hi):
            R = it_total.get(q, 0)
            for it, nodes in t.get(q, {}).items():
                if it * 3 < R:
                    cnt.update(nodes); tot += len(nodes)
        return cnt, tot
    hot = {}
    Ns = [1000, 3000, 10000, 30000, 100000, 300000, 1000000, 3000000]
    for name, t, itt in (('nocache', tr, iters), ('cache', ctr, iters)):
        train, _ = early_reads(t, itt, 5000, 10000)
        test, ttot = early_reads(t, itt, 0, 5000)
        order = [i for i, _ in train.most_common()]
        cov = []
        for N in Ns:
            s = set(order[:N]); cov.append(sum(c for i, c in test.items() if i in s) / max(ttot, 1))
        hot[name] = {'early_reads_test': ttot, 'early_reads_per_query': ttot / 5000, 'distinct_train': len(train),
                     'N': Ns, 'coverage': cov}
        print(name, 'early reads/query', round(ttot / 5000, 1), 'coverage', [round(c, 3) for c in cov], flush=True)

    out = {'explog': str(a.explog), 'trace': str(a.trace), 'ctrace': str(a.ctrace), 'W': W,
           'replay_mismatches': mism, 'equal_rounds': same,
           'profile_cols': ['reads', 'new', 'visible_nocache', 'visible_cache', 'new_visible_cache', 'rounds'],
           'profile': prof.tolist(),
           'lookups_per_query': {k: float(np.mean(v)) for k, v in lookups.items()},
           'distinct_lookup_nodes': {k: len(v) for k, v in uni.items()},
           'distinct_curve': uni_curve, 'hot': hot}
    a.out.write_text(json.dumps(out))


if __name__ == '__main__':
    main()
