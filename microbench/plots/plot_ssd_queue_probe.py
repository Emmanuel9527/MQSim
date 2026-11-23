#!/usr/bin/env python3
"""T500 E11 (realssd/ssd_queue_probe.sh, 2026-11-23): 4 KiB random reads (4 jobs x QD 64) plus 8 KiB reads evenly
spaced or in bursts, with all jobs on separate CPUs / NVMe queues ('sep') or on one CPU / one queue ('same').
(a) 4 KiB reads displaced per 8 KiB read; (b) change of the 4 KiB median latency.
Usage: plots/plot_ssd_queue_probe.py <E11 dir> <out_dir>
"""
import csv, sys
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10, 'axes.spines.top': False,
                     'axes.spines.right': False})
D, OUT = Path(sys.argv[1]), Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
S = {(r['run'], r['group']): r for r in csv.DictReader(open(D / 'summary.csv'))}
g = lambda run, grp, k: float(S[(run, grp)][k])
alone = {'sep': (g('E11_sep_alone', 'rand4k', 'iops') + g('E11_sep_alone_again', 'rand4k', 'iops')) / 2,
         'same': g('E11_same_alone', 'rand4k', 'iops')}
p50 = {pl: g(f'E11_{pl}_alone', 'rand4k', 'lat_p50_us') for pl in ('sep', 'same')}
pats = [('spread', 'evenly spaced\n10k/s'), ('burst32', 'bursts of 32'), ('burst128', 'bursts of 128')]
res = {}
for pl in ('sep', 'same'):
    for p, _ in pats:
        run = f'E11_{pl}_{p}'
        r4, r8 = g(run, 'rand4k', 'iops'), g(run, 'mix8k', 'iops')
        res[(pl, p)] = ((alone[pl] - r4) / r8, g(run, 'rand4k', 'lat_p50_us') - p50[pl], r8)
for k, v in res.items(): print(k, [round(x, 1) for x in v])
fig, ax = plt.subplots(1, 2, figsize=(12, 4.4))
x = np.arange(len(pats)); w = 0.36
lab = {'sep': f'separate queues (4 KiB alone {alone["sep"] / 1e3:.0f}k, SSD saturated)',
       'same': f'one queue, one CPU (4 KiB alone {alone["same"] / 1e3:.0f}k, host-CPU bound)'}
col = {'sep': '#8fa9c4', 'same': '#4a5a6a'}
for i, pl in enumerate(('sep', 'same')):
    for j, (a, idx) in enumerate([(ax[0], 0), (ax[1], 1)]):
        v = [res[(pl, p)][idx] for p, _ in pats]
        a.bar(x + (i - 0.5) * w, v, w, color=col[pl], label=lab[pl])
        for xx, vv in zip(x + (i - 0.5) * w, v): a.text(xx, vv + (0.5 if idx == 0 else 0.6), f'{vv:.1f}', ha='center', fontsize=8)
for a in ax: a.set_xticks(x); a.set_xticklabels([t for _, t in pats]); a.grid(alpha=0.3, axis='y')
ax[0].set_ylabel('4 KiB reads displaced per 8 KiB read'); ax[0].set_ylim(0, 40); ax[0].legend(frameon=False, fontsize=8, loc='upper right')
ax[0].set_title('(a) throughput cost (only comparable within a placement)', fontsize=10, loc='left')
ax[1].set_ylabel('4 KiB median latency change (us)')
ax[1].set_title('(b) 4 KiB p50 latency vs the same placement alone', fontsize=10, loc='left')
fig.tight_layout(); fig.savefig(OUT / 'ssd_queue_probe_t500.png', dpi=130); plt.close(fig)
print('wrote', OUT / 'ssd_queue_probe_t500.png')
