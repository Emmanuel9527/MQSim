#!/usr/bin/env python3
"""Can the SSD avoid a tR the host could not? (2026-11-23, server A1.2 graph, 1000 queries, W4)
  (a) late decision: reads saved at the same recall when each read not yet started is re-checked against
      1.2 x the K-th candidate as updated by the reads already completed (host-quality distances, upper bound)
  (b) tR sharing: QPS gain from an SSD page buffer (64 MB) vs a host cache of the same DRAM, and both (T16 W4)
Usage: plots/plot_ssd_skip_tr.py <results/autonomous_20261123> <out_dir>
"""
import json, sys
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10, 'axes.spines.top': False,
                     'axes.spines.right': False})
C = dict(c1n='#4a5a6a', c1r='#8fa9c4', c2r='#d9a066', c3r='#bbbbbb', s1='#9cbf9a', s2='#5f8f5c',
         ssd='#e07a5f', host='#8fa9c4', both='#4a5a6a')
R, OUT = Path(sys.argv[1]), Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)

def curve(d, cfg):
    pts = []
    for f in Path(d).glob(f'{cfg}_L*.json'):
        if f.stem.rsplit('_L', 1)[0] != cfg: continue
        m = json.load(open(f)); pts.append((m['mean']['recall'], m['mean']['reads'], m['L']))
    return sorted(pts)

fig, ax = plt.subplots(1, 2, figsize=(13, 4.6))
a = ax[0]
for base, runs in [('far1.2', [('far1.2_late1.2_c1near', 'serialized, nearest first (best case)', C['c1n'], '-'),
                               ('far1.2_late1.2_c1rand', 'serialized, random order', C['c1r'], '-'),
                               ('far1.2_late1.2_c2rand', '2 in flight, random', C['c2r'], '-'),
                               ('far1.2_late1.2_c3rand', '3 in flight, random', C['c3r'], '-')]),
                   ('sector10_far1.2', [('sector10_far1.2_late1.2_c1rand', 'on sector packing: serialized, random', C['s1'], '--'),
                                        ('sector10_far1.2_late1.2_c2rand', 'on sector packing: 2 in flight, random', C['s2'], '--')])]:
    b = curve(R / 'hostcombo', base); br = np.array([p[0] for p in b]); bn = np.array([p[1] for p in b])
    for cfg, lab, col, ls in runs:
        pts = [(100 * r, 100 * (n / np.interp(r, br, bn) - 1)) for r, n, L in curve(R / 'late', cfg) if br[0] <= r <= br[-1]]
        a.plot([p[0] for p in pts], [p[1] for p in pts], marker='o', ms=4, ls=ls, color=col, label=lab)
a.axhline(0, color='black', lw=0.8)
a.axhspan(-3, 0, color='#eeeeee', zorder=0); a.text(95.0, -2.85, 'below the 3 % bar set before the run', fontsize=8, color='#777777')
a.set_xlabel('recall@10 (%)'); a.set_ylabel('reads vs host distance termination (%)'); a.set_ylim(-6, 1); a.grid(alpha=0.3)
a.set_title('(a) late decision just before tR (W = 4, upper bound)', fontsize=10, loc='left')
a.legend(frameon=False, fontsize=7.5, loc='lower right')

b = ax[1]
T = lambda n: {(r['run'], r['load']): r for r in map(json.loads, open(R / 'tr_share' / f'{n}.jsonl'))}
base = {(r['run'], r['load']): r for r in map(json.loads, open(R / 'hostcombo' / 'timing.jsonl'))}
rows = [('default_L100', 'DiskANN\nL100', 'oos_van_hc170k', 'oos_van_hc170k_pk4096'),
        ('far1.2_L100', 'termination 1.2x\nL100', 'oos_van_hc170k', 'oos_van_hc170k_pk4096'),
        ('sector10_far1.2_L100', 'sector packing +\ntermination, L100', 'oos_s10_hc16k', 'oos_s10_hc16k_pk4096'),
        ('sector10_far1.2_L200', 'sector packing +\ntermination, L200', 'oos_s10_hc16k', 'oos_s10_hc16k_pk4096')]
pm = T('pk_buf0'); pk = T('pk_buf4096')
x = np.arange(len(rows)); w = 0.2
for i, (lab, col, get) in enumerate([
        ('SSD: merge same-page reads in flight', '#dddddd', lambda r, h, hb: pm[(r, 'T16_W4')]),
        ('SSD: page buffer 64 MB', C['ssd'], lambda r, h, hb: pk[(r, 'T16_W4')]),
        ('host: node / sector cache 64 MB', C['host'], lambda r, h, hb: T(h)[(r, 'T16_W4')]),
        ('host cache 64 MB + SSD buffer 64 MB', C['both'], lambda r, h, hb: T(hb)[(r, 'T16_W4')])]):
    v = [100 * (get(r, h, hb)['qps'] / base[(r, 'T16_W4')]['qps'] - 1) for r, _, h, hb in rows]
    bars = b.bar(x + (i - 1.5) * w, v, w, color=col, label=lab, edgecolor='#999999' if i == 0 else None, lw=0.5)
    for xx, vv in zip(x + (i - 1.5) * w, v): b.text(xx, vv + 0.2, f'{vv:.1f}', ha='center', fontsize=7)
b.set_xticks(x); b.set_xticklabels([r[1] for r in rows], fontsize=8)
b.set_ylabel('QPS gain at the same recall (%)'); b.grid(alpha=0.3, axis='y')
b.set_title('(b) tR sharing vs host cache of the same DRAM (T16 W4)', fontsize=10, loc='left')
b.legend(frameon=False, fontsize=7.5, loc='upper right')
fig.tight_layout(); fig.savefig(OUT / 'ssd_skip_tr.png', dpi=130); plt.close(fig)
print('wrote', OUT / 'ssd_skip_tr.png')
