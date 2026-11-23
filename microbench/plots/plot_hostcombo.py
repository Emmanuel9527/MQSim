#!/usr/bin/env python3
"""Host-only improvements combined (2026-11-23): distance termination (--skip_far) and 4 KiB sector packing with
host filter, alone and together, on the server A1.2 graph; recall vs QPS (ladder_coloc, T16 W4 and T8 W4).
Usage: plots/plot_hostcombo.py <results/autonomous_20261123/hostcombo> <out_dir>
"""
import json, sys
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10, 'axes.spines.top': False,
                     'axes.spines.right': False})
C = dict(base='#7a7a7a', host='#d9a066', green='#9cbf9a', dark='#4a5a6a', ssd='#e07a5f')
R, OUT = Path(sys.argv[1]), Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
t = [json.loads(l) for l in open(R / 'timing.jsonl')]
fig, axes = plt.subplots(1, 2, figsize=(12, 4.6), sharey=True)
for ax, load in zip(axes, ('T16_W4', 'T8_W4')):
    for cfg, lab, col, ls, mk, lw in [('default', 'DiskANN (L = 50 ... 250)', '#b0b0b0', '-', 'o', 1.6),
                                      ('far1.2', 'distance termination (skip if > 1.2 x K-th)', '#e2b45c', '-', 's', 1.6),
                                      ('sector10', '4 KiB sector packing + host filter', '#8fb8de', '-', 'D', 1.6),
                                      ('sector10_far1.3', 'both, termination 1.3 x', '#5b7fa8', '--', '^', 1.8),
                                      ('sector10_far1.2', 'both, termination 1.2 x', '#d0694a', '-', '^', 2.4)]:
        pts = sorted((r['qps'], 100 * r['recall']) for r in t if r['load'] == load and r['run'].rsplit('_L', 1)[0] == cfg)
        ax.plot([p[0] for p in pts], [p[1] for p in pts], ls=ls, marker=mk, color=col, label=lab, ms=5, lw=lw)
    ax.set_title(f'{load.replace("_", " ")}: host-only improvements (server graph, 1000 queries)', fontsize=10, loc='left')
    ax.set_xlabel('QPS (calibrated NAND model)'); ax.grid(alpha=0.3)
axes[0].set_ylabel('recall@10 (%)'); axes[0].set_ylim(94, 99.5)
axes[0].legend(fontsize=8, frameon=False, loc='lower left')
fig.tight_layout(); fig.savefig(OUT / 'hostcombo_qps.png', dpi=130); plt.close(fig)
print('wrote', OUT / 'hostcombo_qps.png')
