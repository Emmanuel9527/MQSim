#!/usr/bin/env python3
"""Two summary figures for the advisor meeting (2026-11-23), server A1.2 graph, W4:
  page_filter_vs_host.png  recall vs QPS (T16 W4, calibrated NAND model): DiskANN, host-only combination
                           (4 KiB sector packing + host filter + distance termination 1.2x), and 16 KiB page packing with
                           SSD-side filter (8 B code / host-grade 31 B code) + distance termination 1.2x
  skip_reads_summary.png   reads saved at the same recall: oracle, host rule, SSD rule; extra on top of the host rule
                           (late decision); tR sharing vs host cache (QPS)
Numbers for the second figure are the ranges recorded in reports/2026-11-22_ssd_pq_footprint.md (sections 6, 9).
Usage: plots/plot_teacher_summary.py <results/autonomous_20261123/hostcombo/timing_all.jsonl> <out_dir>
"""
import json, sys
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10, 'axes.spines.top': False,
                     'axes.spines.right': False, 'axes.edgecolor': '#999999', 'xtick.color': '#555555', 'ytick.color': '#555555'})
T, OUT = Path(sys.argv[1]), Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
rows = [json.loads(l) for l in open(T) if json.loads(l)['load'] == 'T16_W4']
fig, ax = plt.subplots(figsize=(7.6, 4.8))
for cfg, lab, col, ls, mk in [('default', 'DiskANN (L = 50 ... 250)', '#b5b5b5', '-', 'o'),
                              ('page40_PQ8x8_far1.2', 'SSD: 16 KiB page filter, 8 B code + termination', '#e8b27a', '--', 's'),
                              ('page40_server_far1.2', 'SSD: 16 KiB page filter, 31 B code (3.1 GB) + termination', '#e07a5f', '-', 's'),
                              ('sector10_far1.2', 'host only: 4 KiB sector packing + termination', '#6b8db5', '-', '^')]:
    pts = sorted((r['qps'], 100 * r['recall']) for r in rows if r['run'].rsplit('_L', 1)[0] == cfg)
    ax.plot([p[0] for p in pts], [p[1] for p in pts], ls=ls, marker=mk, color=col, label=lab, ms=5)
ax.set_xlabel('QPS (T16 W4, calibrated NAND model)'); ax.set_ylabel('recall@10 (%)'); ax.set_ylim(94.5, 99.3)
ax.grid(alpha=0.3); ax.legend(frameon=False, fontsize=8, loc='lower left')
ax.set_title('SSD-side page filter vs host-only combination (server graph)', fontsize=10, loc='left')
fig.tight_layout(); fig.savefig(OUT / 'page_filter_vs_host.png', dpi=130); plt.close(fig)

fig, ax = plt.subplots(1, 3, figsize=(14, 4.2), gridspec_kw={'width_ratios': [1.25, 1, 1]})
def bars(a, items, xlabel, title):
    for i, (lab, lo, hi, col) in enumerate(items):
        a.barh(i, hi, color=col, alpha=0.4, height=0.6); a.barh(i, lo, color=col, height=0.6)
        a.text(hi + 0.4, i, f'{lo:g}-{hi:g}%' if lo != hi else f'{hi:g}%', va='center', fontsize=9)
    a.set_yticks(range(len(items))); a.set_yticklabels([x[0] for x in items], fontsize=9); a.invert_yaxis()
    a.set_xlabel(xlabel); a.grid(alpha=0.3, axis='x'); a.set_title(title, fontsize=10, loc='left')
    a.set_xlim(0, max(x[2] for x in items) * 1.3)
bars(ax[0], [('oracle: skip true dead reads\n(needs the future)', 19, 25, '#c9d6e3'),
             ('host: PQ distance > 1.2 x 10th\n(= Adaptive Beam Search)', 12, 17, '#8fa9c4'),
             ('SSD: nearest neighbour visited\n+ > 1.2 x 10th', 7, 10, '#e07a5f')],
     'fewer reads at the same recall (W4, L100-200)', '(a) skip reads: vs DiskANN')
bars(ax[1], [('SSD late decision before tR\n(best case: serialized)', 2.3, 4.2, '#e07a5f'),
             ('SSD late decision before tR\n(2 reads in flight)', 0.1, 1.5, '#e8b4a6')],
     'extra fewer reads (dark = low end)', '(b) on top of the host rule')
bars(ax[2], [('SSD page buffer 64 MB', 5.1, 6.2, '#e07a5f'),
             ('host cache 64 MB', 9.9, 11.8, '#8fa9c4')],
     'QPS gain, same recall (T16 W4, L100)', '(c) tR sharing vs host cache')
fig.tight_layout(); fig.savefig(OUT / 'skip_reads_summary.png', dpi=130); plt.close(fig)
print('wrote', OUT / 'page_filter_vs_host.png', OUT / 'skip_reads_summary.png')
