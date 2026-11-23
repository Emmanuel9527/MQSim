#!/usr/bin/env python3
"""T500 E8 (realssd/ssd_controller_probe.sh, 2026-11-23): saturating 4 KiB random reads (QD 256) plus a rate-limited
second reader of 4K / 8K / 16K. (a) 4 KiB IOPS left vs the second reader's achieved IOPS; (b) 4 KiB reads displaced
per added read, against what two cost models predict.
Usage: plots/plot_ssd_ctl_probe.py <probe dir with summary.csv> <out_dir>
"""
import csv, sys
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10, 'axes.spines.top': False,
                     'axes.spines.right': False, 'axes.edgecolor': '#999999', 'xtick.color': '#555555', 'ytick.color': '#555555'})
C = {'4k': '#b0b0b0', '8k': '#d0694a', '16k': '#6b8db5'}
D, OUT = Path(sys.argv[1]), Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
S = {(r['run'], r['group']): float(r['iops']) for r in csv.DictReader(open(D / 'summary.csv'))}
alone = (S[('E8_rand4k_qd256_alone', 'E8_rand4k_qd256_alone')] + S[('E8_rand4k_qd256_alone_again', 'E8_rand4k_qd256_alone_again')]) / 2
fig, ax = plt.subplots(1, 2, figsize=(12.5, 4.5))
for bs in ('4k', '8k', '16k'):
    pts = [(0, alone)] + [(S[(f'E8_mix{bs}_r{r}', f'mix{bs}')], S[(f'E8_mix{bs}_r{r}', 'rand4k')]) for r in (10000, 20000, 40000)]
    ax[0].plot([p[0] / 1e3 for p in pts], [p[1] / 1e3 for p in pts], 'o-', color=C[bs], lw=2, label=f'{bs.upper().replace("K", " KiB")} reads mixed in')
    disp = [((alone - y) / x, x) for x, y in pts[1:]]
    ax[1].plot([d[1] / 1e3 for d in disp], [d[0] for d in disp], 'o-', color=C[bs], lw=2, label=f'{bs.upper().replace("K", " KiB")} mixed in')
a = ax[0]; a.set_xlabel('second reader, achieved IOPS (k)'); a.set_ylabel('4 KiB random read IOPS left (k)')
a.set_ylim(0, 1150); a.grid(alpha=0.3); a.legend(frameon=False, fontsize=8, loc='lower left')
a.set_title('(a) a few large reads take most of the 4 KiB throughput', fontsize=10, loc='left')
b = ax[1]
for y, lab, col in [(1, 'any model: 4 KiB displaces 1', '#999999'), (2, 'data-proportional: 8 KiB <= 2', '#d0694a'),
                    (4, 'data-proportional: 16 KiB <= 4', '#6b8db5'), (6.0, 'additive per-command time: 8 KiB ~6', '#d0694a'),
                    (7.7, 'additive per-command time: 16 KiB ~7.7', '#6b8db5')]:
    b.axhline(y, color=col, ls=':' if y in (2, 4, 1) else '--', lw=1)
    b.text(41, y - 1.1 if y == 1 else y + 0.4, lab, fontsize=7, color=col, ha='right')
b.set_xlabel('second reader, achieved IOPS (k)'); b.set_ylabel('4 KiB reads displaced per added read'); b.set_xlim(0, 42)
b.set_ylim(0, 36); b.grid(alpha=0.3); b.legend(frameon=False, fontsize=8, loc='upper right')
b.set_title('(b) measured displacement vs two cost models', fontsize=10, loc='left')
fig.tight_layout(); fig.savefig(OUT / 'ssd_ctl_probe_t500.png', dpi=130); plt.close(fig)
print('wrote', OUT / 'ssd_ctl_probe_t500.png', 'alone', alone)
