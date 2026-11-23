#!/usr/bin/env python3
"""Analysis D (2026-11-22): the SSD walks the first k rounds with a coarse code, then hands over to the host's 31-B PQ.
Server A1.2 graph, W4 L100, 1000 queries (ssdpq/graphsim.py --ssd_rounds k --ssd_spec <code>).
  (a) recall vs code length: coarse code for the first 5 rounds only, vs for the whole search
  (b) cost of the SSD code: extra reads per query vs DiskANN at the same recall (interpolated on DiskANN's own L sweep,
      so a change of recall is accounted for), per code length and k; line = mean of the codes of one length,
      dots = individual codes (4 B: PQ4x8, PQ8x4; 8 B: PQ8x8, PQ16x4)
(2026-11-23: replaces the earlier 3-panel version, whose 'extra reads vs control' and 'reads vs recall' panels
 said the same thing twice.)
Usage: plots/plot_ssd_autonomy.py <results/autonomous_20261122/server/D> <out_dir>
"""
import json, sys
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10, 'axes.spines.top': False,
                     'axes.spines.right': False, 'axes.edgecolor': '#999999', 'xtick.color': '#555555',
                     'ytick.color': '#555555'})
D, OUT = Path(sys.argv[1]), Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
m = lambda name: json.load(open(D / f'{name}.json'))['mean']
b = sorted((m(f'base_L{L}')['recall'], m(f'base_L{L}')['reads']) for L in (50, 70, 100, 120, 150, 200))
br, bn = np.array([x[0] for x in b]), np.array([x[1] for x in b])
iso = lambda r: 100 * (r['reads'] / np.interp(r['recall'], br, bn) - 1)
b100 = 100 * m('base_L100')['recall']
fig, ax = plt.subplots(1, 2, figsize=(12.5, 4.6))

a = ax[0]
b8 = ['PQ4x8', 'PQ8x8', 'PQ16x8']; L = {'PQ4x8': 4, 'PQ8x8': 8, 'PQ16x8': 16}
a.plot([L[c] for c in b8] + [31], [100 * m(f'ssd_k5_{c}')['recall'] for c in b8] + [100 * m('ssd_k5_server')['recall']],
       'o-', color='#8fa9c4', lw=2, label='coarse code for the first 5 rounds, then host 31 B PQ')
a.plot([L[c] for c in b8] + [31], [100 * m(f'fullcoarse_{c}')['recall'] for c in b8] + [b100],
       's-', color='#d0694a', lw=2, label='coarse code for the whole search')
for c in b8:
    a.annotate(f'{100 * m(f"fullcoarse_{c}")["recall"]:.0f}%', (L[c], 100 * m(f'fullcoarse_{c}')['recall']),
               textcoords='offset points', xytext=(8, -4), fontsize=9, color='#d0694a')
a.axhline(b100, color='#b0b0b0', ls=':', lw=1); a.text(4.1, b100 - 7, f'DiskANN: {b100:.1f}%', fontsize=9, color='#888888')
a.set_xscale('log', base=2); a.set_xticks([4, 8, 16, 31]); a.set_xticklabels(['4 B', '8 B', '16 B', '31 B\n(host)'])
a.set_xlabel('SSD-side code length per vector'); a.set_ylabel('recall@10 (%)'); a.set_ylim(0, 105); a.grid(alpha=0.3)
a.set_title('(a) coarse codes only work in the early rounds', fontsize=10, loc='left')
a.legend(frameon=False, fontsize=8, loc='lower right')

c = ax[1]
groups = [('4 B', ['PQ4x8', 'PQ8x4']), ('8 B', ['PQ8x8', 'PQ16x4']), ('16 B', ['PQ16x8']), ('31 B\n(host)', ['server']),
          ('exact', ['exact'])]
x = np.arange(len(groups))
col = {3: '#b5cce2', 5: '#6b8db5', 8: '#d0694a'}
for k in (3, 5, 8):
    means = []
    for i, (_, cs) in enumerate(groups):
        v = [iso(m(f'ssd_k{k}_{cc}')) for cc in cs]
        means.append(np.mean(v))
        if len(v) > 1: c.scatter([i] * len(v), v, s=14, color=col[k], alpha=0.6, zorder=2)
    c.plot(x, means, 'o-', color=col[k], lw=2, ms=6, label=f'SSD walks the first {k} rounds', zorder=3)
    c.annotate(f'{means[0]:+.0f}%', (0, means[0]), textcoords='offset points', xytext=(-34, -3), fontsize=9, color=col[k])
c.axhline(0, color='#888888', lw=0.8); c.text(4.25, 0.4, 'same as DiskANN', fontsize=8, color='#888888', ha='right')
c.set_xticks(x); c.set_xticklabels([g for g, _ in groups]); c.set_xlim(-0.6, 4.4)
c.set_xlabel('code the SSD navigates with'); c.set_ylabel('extra reads vs DiskANN at the same recall (%)'); c.grid(alpha=0.3)
c.set_title('(b) cost of navigating with a coarse code', fontsize=10, loc='left')
c.legend(frameon=False, fontsize=8, loc='upper right')
fig.tight_layout(); fig.savefig(OUT / 'ssd_autonomy_D.png', dpi=130); plt.close(fig)
print('wrote', OUT / 'ssd_autonomy_D.png')
