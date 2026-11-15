#!/usr/bin/env python3
"""P1: how well can the reads of a later DiskANN round be predicted, and by whom?

Input: an explog directory from diskann_policy_sweep.sh (expansions.bin + .queries.csv + trace_access.csv, original
frontier policy, no node cache). The host candidate list is replayed offline from the expansion log; the replayed
frontier of every iteration must equal trace_access.csv (checked, reported).

Predictors (each uses only what it could know at prediction time):
  H        host hint, sent with round r: the next k unexpanded candidates after round r's frontier.
  S-full   SSD that mirrors the host candidate list (sees every read, has PQ for every neighbour), predicting after
           round r's reads: d=1 -> the first k unexpanded after inserting round r's neighbours; d=2 -> the k after
           the next frontier. This is the in-SSD traversal bound.
  S-local  SSD with PQ for neighbours but no host list: round r's newly seen neighbours ranked by PQ distance.
  S-noPQ   SSD without PQ: round r's newly seen neighbours, round-robin over parents in arrival order, each in
           adjacency-list order.
Targets: frontier of round r+d (d = 1, 2). A target counts as covered when its sector is among the predicted
sectors (a prefetch reads whole sectors). Metrics, summed over all rounds that have a round r+d:
  coverage        covered target reads / target reads
  full_round      rounds whose every target read is covered (what a barrier needs)
  precision       predicted sectors read in round r+d / predicted sectors
  used_within_3   predicted sectors read in any of rounds r+1..r+3 / predicted sectors
Usage: analyze_prefetch_prediction.py <explog_dir> [--W 4] [--L 100] [--queries N] [--ks 1 2 4 8]
Writes <explog_dir>/prediction.json and prediction.png
"""
import argparse, bisect, collections, csv, json
from pathlib import Path
import numpy as np

REC = np.dtype([('L', '<u4'), ('q', '<u4'), ('it', '<u4'), ('parent', '<u4'), ('nbr', '<u4'), ('d', '<f4'), ('f', 'u1')])
PREDICTORS = ['H', 'S-full', 'S-local', 'S-noPQ']


def node_sector_fn(access):
    """Default DiskANN layout: sector = 1 + id // nodes_per_sector. Infer nodes_per_sector from the access log."""
    ids = np.array([a[0] for a in access]); secs = np.array([a[1] for a in access])
    m = secs > 1
    for n in range(1, 257):
        if np.all(1 + ids[m] // n == secs[m]):
            return n
    raise SystemExit('Could not infer nodes_per_sector (non-default layout?)')


class CandList:
    """Replica of diskann::NeighborPriorityQueue for unvisited inserts (ordered by (distance, id), capacity L)."""

    def __init__(self, L):
        self.L, self.keys, self.exp = L, [], []

    def insert(self, d, i):
        k = (d, i)
        if len(self.keys) == self.L and self.keys[-1] < k:
            return False
        pos = bisect.bisect_left(self.keys, k)
        self.keys.insert(pos, k); self.exp.insert(pos, False)
        if len(self.keys) > self.L:
            self.keys.pop(); self.exp.pop()
        return pos < self.L

    def unexpanded(self, n, skip=0):
        out = []
        for (d, i), e in zip(self.keys, self.exp):
            if not e:
                if skip: skip -= 1; continue
                out.append(i)
                if len(out) == n: break
        return out

    def take(self, W):
        out = []
        for j, e in enumerate(self.exp):
            if not e:
                self.exp[j] = True; out.append(self.keys[j][1])
                if len(out) == W: break
        return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('dir', type=Path)
    ap.add_argument('--W', type=int, required=True)
    ap.add_argument('--L', type=int, default=100)
    ap.add_argument('--queries', type=int, default=0)
    ap.add_argument('--ks', type=int, nargs='+', default=[1, 2, 4, 8], help='prediction size k as multiples of W')
    a = ap.parse_args()
    d = a.dir.resolve(); W, L = a.W, a.L
    qrows = list(csv.DictReader(open(d / 'expansions.bin.queries.csv')))
    if a.queries: qrows = qrows[:a.queries]
    nrec = sum(int(r['records']) for r in qrows)
    rec = np.fromfile(d / 'expansions.bin', dtype=REC, count=nrec)
    access = collections.defaultdict(list); flat = []
    qset = {int(r['query_id']) for r in qrows}
    with open(d / 'trace_access.csv') as f:
        for r in csv.DictReader(f):
            q = int(r['query_id'])
            if q in qset:
                access[(q, int(r['iteration']))].append(int(r['node_id']))
                if len(flat) < 200000: flat.append((int(r['node_id']), int(r['sector_id'])))
    nps = node_sector_fn(flat)
    sec = lambda i: 1 + i // nps
    ks = [k * W for k in a.ks]
    # acc[(pred, d, k)] = [target_reads, covered, rounds, full_rounds, predicted, precise, used3]
    acc = collections.defaultdict(lambda: np.zeros(7))
    mism = 0; rounds_total = 0; pos = 0
    for r in qrows:
        q, n = int(r['query_id']), int(r['records'])
        x = rec[pos:pos + n]; pos += n
        assert (x['q'] == q).all(), 'record/query misalignment'
        by_it = collections.defaultdict(list)
        for t in x: by_it[int(t['it'])].append(t)
        cl = CandList(L); cl.insert(float(r['start_pq_distance']), int(r['start_node']))
        frontiers, preds = [], []
        for it in range(int(r['iterations'])):
            F = cl.take(W)
            if F != access.get((q, it), []): mism += 1
            frontiers.append(F)
            maxk = max(ks)
            p = {'H': cl.unexpanded(maxk)}                     # sent with round r, before its results
            recs = by_it.get(it, [])
            new = [t for t in recs if t['f'] & 1]
            for t in new: cl.insert(float(t['d']), int(t['nbr']))
            p['S-full'] = cl.unexpanded(maxk)                 # d=1 view; d=2 view below
            p['S-full2'] = cl.unexpanded(maxk, skip=W)
            p['S-local'] = [int(t['nbr']) for t in sorted(new, key=lambda t: (float(t['d']), int(t['nbr'])))][:maxk]
            per_parent = collections.OrderedDict()
            for t in new: per_parent.setdefault(int(t['parent']), []).append(int(t['nbr']))
            rr, lists = [], list(per_parent.values())
            for j in range(max((len(l) for l in lists), default=0)):
                for l in lists:
                    if j < len(l): rr.append(l[j])
            p['S-noPQ'] = rr[:maxk]
            preds.append(p)
        rounds_total += len(frontiers)
        for it in range(len(frontiers)):
            for dd in (1, 2):
                if it + dd >= len(frontiers) or not frontiers[it + dd]: continue
                tgt = [sec(i) for i in frontiers[it + dd]]
                future = set(sec(i) for j in range(it + 1, min(it + 4, len(frontiers))) for i in frontiers[j])
                tset = set(tgt)
                for name in PREDICTORS:
                    lst = preds[it]['S-full2' if (name == 'S-full' and dd == 2) else name]
                    for k in ks:
                        ps = set(sec(i) for i in lst[:k])
                        cov = sum(s in ps for s in tgt)
                        v = acc[(name, dd, k)]
                        v += [len(tgt), cov, 1, cov == len(tgt), len(ps), len(ps & tset), len(ps & future)]
    res = {'dir': str(d), 'W': W, 'L': L, 'queries': len(qrows), 'rounds': rounds_total, 'nodes_per_sector': nps,
           'frontier_replay_mismatches': mism, 'results': []}
    print(f'{len(qrows)} queries, {rounds_total} rounds, frontier replay mismatches: {mism}')
    print(f"{'pred':8s} d  k   coverage  full_round  precision  used_within_3")
    for name in PREDICTORS:
        for dd in (1, 2):
            for k in ks:
                t, c, n, fr, pr, pc, u3 = acc[(name, dd, k)]
                row = dict(predictor=name, d=dd, k=k, coverage=c / t, full_round=fr / n, precision=pc / max(pr, 1),
                           used_within_3=u3 / max(pr, 1))
                res['results'].append(row)
                print(f"{name:8s} {dd}  {k:3d} {row['coverage']:9.3f} {row['full_round']:10.3f} {row['precision']:10.3f} {row['used_within_3']:13.3f}")
    (d / 'prediction.json').write_text(json.dumps(res, indent=2))
    try:
        import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
        plt.rcParams['font.family'] = ['Noto Sans CJK TC', 'Noto Sans CJK JP', 'DejaVu Sans']
        colors = dict(zip(PREDICTORS, ['#2a78d6', '#eb6834', '#1baf7a', '#eda100']))
        marks = dict(zip(PREDICTORS, ['o', 's', '^', 'D']))
        labels = {'H': 'H 主機提示', 'S-full': 'S-full SSD 鏡像候選佇列', 'S-local': 'S-local SSD+PQ', 'S-noPQ': 'S-noPQ SSD 無 PQ'}
        fig, ax = plt.subplots(2, 2, figsize=(12, 8), sharex=True, sharey=True)
        for ci, dd in enumerate((1, 2)):
            for ri, metric in enumerate(('coverage', 'full_round')):
                axx = ax[ri, ci]
                for name in PREDICTORS:
                    rows = [x for x in res['results'] if x['predictor'] == name and x['d'] == dd]
                    axx.plot([x['k'] / W for x in rows], [100 * x[metric] for x in rows], marker=marks[name], ms=8,
                             lw=2, color=colors[name], label=labels[name])
                axx.set_title(f"{'單筆涵蓋率' if metric == 'coverage' else '整輪全部命中'}，預測第 r+{dd} 輪", loc='left')
                axx.grid(alpha=0.25); axx.set_ylim(0, 102)
                for s in ('top', 'right'): axx.spines[s].set_visible(False)
                if ci == 0: axx.set_ylabel('%')
                if ri == 1: axx.set_xlabel(f'預讀筆數 k（× W={W}）')
        ax[0, 0].legend(frameon=False, fontsize=9, loc='lower right')
        fig.suptitle(f'下一輪讀取的可預測性（W={W}, L={L}, {len(qrows)} query；以 sector 計）', fontsize=12)
        fig.tight_layout(); fig.savefig(d / 'prediction.png', dpi=130)
    except ImportError:
        pass


if __name__ == '__main__':
    main()
