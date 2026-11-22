#!/usr/bin/env python3
"""Pipeline-ladder study: turn a DiskANN expansion log into per-round schedules for ladder_sim.py.

Input: an explog directory from server_sweeps/diskann_policy_sweep.sh (expansions.bin, .queries.csv, trace_access.csv; original
frontier policy, no node cache). The host candidate list is replayed offline (same replica as
server_sweeps/analyze_prefetch_prediction.py); every replayed frontier must equal trace_access.csv.

Output (pickle): {'W', 'L', 'nodes_per_sector', 'mismatches', 'queries': [ [round, ...], ... ]}
  round = {'F': frontier sectors (the W reads of this round, in rank order),
           'H': sectors of the next unexpanded candidates after the frontier, known to the host when it sends the
                round (host hint), rank order, up to --kmax entries,
           'SL': sectors of this round's newly seen neighbours ranked by PQ distance (what an SSD holding PQ codes
                 but no host candidate list can rank), up to --kmax entries}
Also: --trace <trace_access.csv> converts a plain access log (any number of queries) into a frontier-only schedule.
Usage: ladder/ladder_prepare.py <explog_dir> --W 4 --out sched_W4.pkl
       ladder/ladder_prepare.py --trace <dir>/trace_access.csv --W 4 --out sched_T16_W4_full.pkl
"""
import argparse, collections, csv, pickle, sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sys as _sys, pathlib as _pl; _sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1] / 'common'))
from explog import REC, CandList, node_sector_fn


def from_trace(path, W, out):
    rounds = collections.defaultdict(lambda: collections.defaultdict(list))
    with open(path) as f:
        for r in csv.DictReader(f):
            rounds[int(r['query_id'])][int(r['iteration'])].append(int(r['sector_id']))
    queries = [[{'F': rounds[q][it], 'H': [], 'SL': []} for it in sorted(rounds[q])] for q in sorted(rounds)]
    pickle.dump({'W': W, 'L': None, 'mismatches': 0, 'queries': queries, 'source': str(path)}, open(out, 'wb'))
    nr = sum(len(q) for q in queries); nrd = sum(len(r['F']) for q in queries for r in q)
    print(f'{len(queries)} queries, rounds/query {nr / len(queries):.2f}, reads/query {nrd / len(queries):.2f} -> {out}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('dir', type=Path, nargs='?')
    ap.add_argument('--trace', type=Path)
    ap.add_argument('--W', type=int, required=True)
    ap.add_argument('--L', type=int, default=100)
    ap.add_argument('--kmax', type=int, default=0, help='hint entries kept per round (default 3W)')
    ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args()
    if a.trace:
        return from_trace(a.trace, a.W, a.out)
    d, W, L = a.dir.resolve(), a.W, a.L
    kmax = a.kmax or 3 * W
    qrows = list(csv.DictReader(open(d / 'expansions.bin.queries.csv')))
    rec = np.fromfile(d / 'expansions.bin', dtype=REC, count=sum(int(r['records']) for r in qrows))
    qset = {int(r['query_id']) for r in qrows}
    access = collections.defaultdict(list); flat = []
    with open(d / 'trace_access.csv') as f:
        for r in csv.DictReader(f):
            q = int(r['query_id'])
            if q in qset:
                access[(q, int(r['iteration']))].append(int(r['node_id']))
                if len(flat) < 200000: flat.append((int(r['node_id']), int(r['sector_id'])))
    nps = node_sector_fn(flat)
    sec = lambda i: 1 + i // nps
    queries, mism, pos = [], 0, 0
    for r in qrows:
        q, n = int(r['query_id']), int(r['records'])
        x = rec[pos:pos + n]; pos += n
        assert (x['q'] == q).all(), 'record/query misalignment'
        by_it = collections.defaultdict(list)
        for t in x: by_it[int(t['it'])].append(t)
        cl = CandList(L); cl.insert(float(r['start_pq_distance']), int(r['start_node']))
        rounds = []
        for it in range(int(r['iterations'])):
            F = cl.take(W)
            if F != access.get((q, it), []): mism += 1
            H = cl.unexpanded(kmax)
            new = [t for t in by_it.get(it, []) if t['f'] & 1]
            for t in new: cl.insert(float(t['d']), int(t['nbr']))
            SL = [int(t['nbr']) for t in sorted(new, key=lambda t: (float(t['d']), int(t['nbr'])))][:kmax]
            rounds.append({'F': [sec(i) for i in F], 'H': [sec(i) for i in H], 'SL': [sec(i) for i in SL]})
        queries.append(rounds)
    pickle.dump({'W': W, 'L': L, 'nodes_per_sector': nps, 'mismatches': mism, 'queries': queries, 'source': str(d)},
                open(a.out, 'wb'))
    nr = sum(len(q) for q in queries); nrd = sum(len(r['F']) for q in queries for r in q)
    print(f'{len(queries)} queries, rounds/query {nr / len(queries):.2f}, reads/query {nrd / len(queries):.2f}, '
          f'frontier replay mismatches {mism}, nodes/sector {nps} -> {a.out}')


if __name__ == '__main__':
    main()
