#!/usr/bin/env python3
"""T500 saturation probe (realssd/ssd_saturation_probe.sh, 2026-11-22 on the lab server): three panels.
  (a) E1: 4 KiB random read IOPS and mean latency vs total queue depth
  (b) E2: random read size sweep at QD 32 / 128: IOPS and cost of one read relative to a 4 KiB read
  (c) E6: QD 16 4 KiB random reads confined to an address range: IOPS and p99 vs range
Usage: plots/plot_ssd_probe.py <probe dir with summary.csv> <out_dir>
"""
import csv, sys
from collections import defaultdict
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10, 'axes.spines.top': False,
                     'axes.spines.right': False})
C = dict(a='#8fa9c4', b='#d9a066', c='#4a5a6a', d='#e07a5f')
D, OUT = Path(sys.argv[1]), Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
S = {r['run'] + ('/' + r['group'] if r['group'] != r['run'] else ''): r for r in csv.DictReader(open(D / 'summary.csv'))}
f = lambda k, c: float(S[k][c])
fig, ax = plt.subplots(1, 3, figsize=(15, 4.3))
qd = [1, 2, 4, 8, 16, 32, 64, 128, 256]
a = ax[0]; a.plot(qd, [f(f'E1_rand4k_qd{q}', 'iops') / 1e3 for q in qd], 'o-', color=C['c'], label='IOPS (k)')
a.set_xscale('log', base=2); a.set_xlabel('total queue depth'); a.set_ylabel('4 KiB random read IOPS (k)')
a2 = a.twinx(); a2.plot(qd, [f(f'E1_rand4k_qd{q}', 'lat_mean_us') for q in qd], 's--', color=C['b'], label='mean latency')
a2.set_ylabel('mean latency (us)'); a2.spines['top'].set_visible(False)
a.set_title('(a) 4 KiB random read vs queue depth', fontsize=10, loc='left'); a.grid(alpha=0.3)
a.legend(loc='upper left', frameon=False, fontsize=8); a2.legend(loc='center left', frameon=False, fontsize=8)
bs = ['4k', '8k', '16k', '32k', '64k']; x = range(len(bs))
b = ax[1]
for q, col in [(32, C['a']), (128, C['c'])]:
    b.plot(x, [f(f'E2_rand{s}_qd{q}', 'iops') / 1e3 for s in bs], 'o-', color=col, label=f'IOPS, QD {q}')
    for i, s in enumerate(bs):
        r = f(f'E2_rand4k_qd{q}', 'iops') / f(f'E2_rand{s}_qd{q}', 'iops')
        if i: b.annotate(f'{r:.1f}x', (i, f(f'E2_rand{s}_qd{q}', 'iops') / 1e3), textcoords='offset points',
                         xytext=(4, 6 if q == 128 else -12), fontsize=7, color=col)
b.set_xticks(list(x)); b.set_xticklabels([s.upper().replace('K', ' KiB') for s in bs]); b.set_yscale('log')
b.set_xlabel('random read size'); b.set_ylabel('IOPS (k, log)'); b.grid(alpha=0.3)
b.set_title('(b) read size sweep (labels: cost of one read vs 4 KiB)', fontsize=10, loc='left')
b.legend(frameon=False, fontsize=8)
rg = [('2M', 2), ('8M', 8), ('32M', 32), ('128M', 128), ('1G', 1024)]
c = ax[2]
io = [sum(f(f'E6_qd16_range{r}_off{k}', 'iops') for k in (1, 2, 3)) / 3e3 for r, _ in rg] + [f('E6_qd16_rangefull', 'iops') / 1e3]
p99 = [sum(f(f'E6_qd16_range{r}_off{k}', 'lat_p99_us') for k in (1, 2, 3)) / 3 for r, _ in rg] + [f('E6_qd16_rangefull', 'lat_p99_us')]
xs = [m for _, m in rg] + [40960]
c.plot(xs, io, 'o-', color=C['c'], label='IOPS (k)'); c.set_xscale('log', base=2)
c.set_xticks(xs); c.set_xticklabels([r for r, _ in rg] + ['full\n(41 GB)'])
c.set_xlabel('address range of the 16 outstanding reads'); c.set_ylabel('IOPS (k)'); c.grid(alpha=0.3)
c2 = c.twinx(); c2.plot(xs, p99, 's--', color=C['d'], label='p99 latency'); c2.set_ylabel('p99 latency (us)')
c2.spines['top'].set_visible(False)
c.set_title('(c) QD 16 reads confined to a range', fontsize=10, loc='left')
c.legend(loc='center right', bbox_to_anchor=(1, 0.62), frameon=False, fontsize=8); c2.legend(loc='center right', bbox_to_anchor=(1, 0.52), frameon=False, fontsize=8)
fig.tight_layout(); fig.savefig(OUT / 'ssd_probe_t500.png', dpi=130); plt.close(fig)
print('wrote', OUT / 'ssd_probe_t500.png')
