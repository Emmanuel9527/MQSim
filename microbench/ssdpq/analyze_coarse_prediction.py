#!/usr/bin/env python3
"""How precise must an SSD-side PQ be to predict the next DiskANN round?

Input: an explog dir (server_sweeps/diskann_policy_sweep.sh: expansions.bin + .queries.csv + trace_access.csv; original policy,
no node cache). The host candidate list is replayed with the host's own PQ distances (field d), giving the true
frontier F[r] of every round (checked against trace_access.csv) and the host hint H[r] (the W unexpanded candidates
right after F[r], known before round r's reads complete).

For round r -> r+1 the SSD has just read F[r] and sees the new (never-seen) neighbours N[r] (records with flag
bit 0). It estimates their distances with a coarse code and predicts the next frontier:
  local   top-k of N[r] by coarse distance (SSD alone, no host information)
  hybrid  top-k of H[r] (host-sent, with host PQ distances) U N[r] (coarse distances)
k = W (exact next frontier) and 2W. Score: node-level coverage of F[r+1] and whole-round hit (all W covered).
The same predictors with the host PQ distance (spec 'hostPQ') are the references; hybrid/hostPQ is ~100% by
construction (it is the host list itself).
Coarse codes are trained on a 200k random sample of the base and applied to every node that appears.
Usage: ssdpq/analyze_coarse_prediction.py <explog_dir> --W 4 --specs hostPQ exact PQ16x8 ... --out <json>
"""
import argparse, collections, csv, json, time
from pathlib import Path
import numpy as np
import sys as _sys, pathlib as _pl; _sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1] / 'common'))
from explog import REC, CandList
import coarse_quant as cq

NBIN = 10


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('dir', type=Path)
    ap.add_argument('--W', type=int, required=True)
    ap.add_argument('--L', type=int, default=100)
    ap.add_argument('--specs', nargs='+', required=True)
    ap.add_argument('--train', type=int, default=200000)
    ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args()
    d, W, L = a.dir.resolve(), a.W, a.L
    qrows = list(csv.DictReader(open(d / 'expansions.bin.queries.csv')))
    nrec = sum(int(r['records']) for r in qrows)
    rec = np.fromfile(d / 'expansions.bin', dtype=REC, count=nrec)
    access = collections.defaultdict(list)
    with open(d / 'trace_access.csv') as f:
        for r in csv.DictReader(f):
            access[(int(r['query_id']), int(r['iteration']))].append(int(r['node_id']))
    ids = np.unique(np.concatenate([rec['nbr'], [int(r['start_node']) for r in qrows]]).astype(np.int64))
    print(f'{len(qrows)} queries, {nrec} records, {len(ids)} distinct nodes', flush=True)
    base, Q = cq.load_base(), cq.load_query()
    t0 = time.time()
    X = cq.gather(base, ids)
    rng = np.random.default_rng(1)
    T = cq.gather(base, np.sort(rng.choice(base.shape[0], a.train, replace=False)))
    print(f'gathered vectors in {time.time() - t0:.0f}s', flush=True)
    rows_all = np.searchsorted(ids, rec['nbr'].astype(np.int64))

    # host replay once: frontiers, hints, per-round new-neighbour record slices
    per_q = []; pos = 0; mism = 0
    for r in qrows:
        q, n = int(r['query_id']), int(r['records'])
        x = rec[pos:pos + n]; idx = np.arange(pos, pos + n); pos += n
        by_it = collections.defaultdict(list)
        for j, t in zip(idx, x):
            if t['f'] & 1: by_it[int(t['it'])].append(j)
        cl = CandList(L); cl.insert(float(r['start_pq_distance']), int(r['start_node']))
        F, H = [], []
        for it in range(int(r['iterations'])):
            f = cl.take(W)
            if f != access.get((q, it), []): mism += 1
            F.append(f)
            H.append([(k[0], k[1]) for k, e in zip(cl.keys, cl.exp) if not e][:2 * W])
            for j in by_it.get(it, []): cl.insert(float(rec[j]['d']), int(rec[j]['nbr']))
        per_q.append((q, F, H, by_it))
    print(f'replay mismatches: {mism}', flush=True)

    res = {'dir': str(d), 'W': W, 'L': L, 'queries': len(qrows), 'replay_mismatches': mism, 'specs': {}}
    for spec in a.specs:
        t0 = time.time()
        if spec == 'hostPQ':
            dist = rec['d'].astype(np.float64); nbytes = 31
        else:
            qz = cq.make(spec).train(T)
            qz.encode(X)
            dist = np.empty(nrec); nbytes = qz.bytes; pos = 0
            for r in qrows:
                q, n = int(r['query_id']), int(r['records'])
                dist[pos:pos + n] = qz.dist(Q[q], rows_all[pos:pos + n]); pos += n
            # rank fidelity vs host PQ on new neighbours (Spearman-free: pairwise order agreement on a sample)
        m = (rec['f'] & 1).astype(bool)
        samp = np.random.default_rng(2).choice(np.flatnonzero(m), 200000, replace=False)
        rel = float(np.median(np.abs(dist[samp] - rec['d'][samp]) / np.maximum(rec['d'][samp], 1)))
        # acc[pred][k] = [targets, covered, rounds, full]; per-bin and early (first 5 rounds) too
        acc = {p: {k: np.zeros(4) for k in (W, 2 * W)} for p in ('local', 'hybrid')}
        binacc = {p: np.zeros((NBIN, 2)) for p in ('local', 'hybrid')}
        early = {p: np.zeros(2) for p in ('local', 'hybrid')}
        for q, F, H, by_it in per_q:
            R = len(F)
            for it in range(R - 1):
                tgt = F[it + 1]
                if not tgt: continue
                N = by_it.get(it, [])
                loc = sorted(((dist[j], int(rec[j]['nbr'])) for j in N))
                seen = {i for _, i in loc}
                hyb = sorted(loc + [h for h in H[it] if h[1] not in seen])
                b = min(NBIN - 1, it * NBIN // R)
                for p, lst in (('local', loc), ('hybrid', hyb)):
                    for k in (W, 2 * W):
                        ps = {i for _, i in lst[:k]}
                        c = sum(i in ps for i in tgt)
                        acc[p][k] += [len(tgt), c, 1, c == len(tgt)]
                        if k == W:
                            binacc[p][b] += [len(tgt), c]
                            if it < 5: early[p] += [len(tgt), c]
        out = {'bytes': nbytes, 'median_rel_err_vs_hostPQ': rel, 'seconds': time.time() - t0}
        for p in acc:
            for k in acc[p]:
                t, c, n, fr = acc[p][k]
                out[f'{p}_k{k // W}W_coverage'] = c / t
                out[f'{p}_k{k // W}W_full_round'] = fr / n
            out[f'{p}_bins'] = (binacc[p][:, 1] / np.maximum(binacc[p][:, 0], 1)).tolist()
            out[f'{p}_early5'] = early[p][1] / max(early[p][0], 1)
        res['specs'][spec] = out
        print(spec, json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in out.items()
                                if not k.endswith('bins')}), flush=True)
        a.out.write_text(json.dumps(res, indent=1))


if __name__ == '__main__':
    main()
