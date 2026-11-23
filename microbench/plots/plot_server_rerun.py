#!/usr/bin/env python3
"""Server-graph rerun (2026-11-22): avoidable reads (E) and co-located expansion timing (C) on the server A1.2 index.
Usage: plots/plot_server_rerun.py <results/autonomous_20261122/server> <out_dir>
Writes server_avoidable_reads.png (recall vs reads, W4 and W8) and server_coloc_timing.png (recall vs QPS, T16 W4).
"""
import glob, json, sys
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10, 'axes.spines.top': False,
                     'axes.spines.right': False})
C = dict(base='#7a7a7a', host='#d9a066', oracle='#8fa9c4', ssd='#e07a5f', dark='#4a5a6a', green='#9cbf9a',
         light='#b8cfe0')
R, OUT = Path(sys.argv[1]), Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)

# ---- E: recall vs reads ----
rows = [json.load(open(f)) for f in glob.glob(str(R / 'E/W*.json'))]
def curve(W, skip):
    pts = sorted((r['mean']['reads'], 100 * r['mean']['recall']) for r in rows if r['W'] == W and r['skip'] == skip)
    return [p[0] for p in pts], [p[1] for p in pts]
fig, axes = plt.subplots(1, 2, figsize=(12, 4.4), sharey=True)
for ax, W in zip(axes, (4, 8)):
    for skip, lab, col, ls, mk in [('none', 'DiskANN (L = 50 ... 200)', C['base'], '-', 'o'),
                                   ('far:1.2', 'host rule: skip if PQ dist > 1.2 x K-th', C['host'], '-', 's'),
                                   ('dead_far:1.1', 'oracle: skip dead reads beyond 1.1 x K-th', C['oracle'], '--', 'D'),
                                   ('nbrvis:1:1.2', 'SSD rule: nearest neighbour visited and > 1.2 x K-th', C['ssd'], '-', '^'),
                                   ('nbrvis:4:1.2', 'SSD rule: 4 nearest visited and > 1.2 x K-th', C['ssd'], ':', 'v')]:
        x, y = curve(W, skip)
        if x: ax.plot(x, y, ls=ls, marker=mk, color=col, label=lab, ms=5)
    ax.set_title(f'W = {W}: reads per query vs recall@10 (server graph, 1000 queries)', fontsize=10, loc='left')
    ax.set_xlabel('SSD reads per query'); ax.grid(alpha=0.3)
axes[0].set_ylabel('recall@10 (%)'); axes[0].set_ylim(93, 99)
axes[1].legend(fontsize=8, loc='lower right', frameon=False)
fig.tight_layout(); fig.savefig(OUT / 'server_avoidable_reads.png', dpi=130); plt.close(fig)

# ---- C: recall vs QPS (T16 W4) ----
t = [json.loads(l) for l in open(R / 'C/timing.jsonl')]
t = [r for r in t if r['load'] == 'T16_W4']
def ccurve(cfg):
    pts = sorted((100 * r['recall'], r['qps']) for r in t if r['run'].rsplit('_L', 1)[0] == cfg)
    return [p[1] for p in pts], [p[0] for p in pts]
fig, ax = plt.subplots(figsize=(8.5, 4.8))
for cfg, lab, col, ls, mk in [('default', 'DiskANN, vanilla layout', C['base'], '-', 'o'),
                              ('page40_noexp', '16 KiB page packing, no expansion', C['light'], '-', 'o'),
                              ('sector10_expsector_server', '4 KiB sector packing + host filter (host-only)', C['green'], '-', 's'),
                              ('page40_exp_server', '16 KiB page packing + filter, host-grade 31 B code', C['ssd'], '-', '^'),
                              ('page40_exp_PQ8x8', '16 KiB page packing + filter, 8 B code in SSD', C['ssd'], '--', '^'),
                              ('page40_exp_PQ4x8', '16 KiB page packing + filter, 4 B code in SSD', C['ssd'], ':', '^')]:
    x, y = ccurve(cfg)
    ax.plot(x, y, ls=ls, marker=mk, color=col, label=lab, ms=5)
ax.set_xlabel('QPS (T16 W4, calibrated NAND model)'); ax.set_ylabel('recall@10 (%)'); ax.grid(alpha=0.3)
ax.set_title('Co-located expansion on the server graph (L = 50, 70, 100, 150)', fontsize=10, loc='left')
ax.legend(fontsize=8, frameon=False, loc='lower left')
fig.tight_layout(); fig.savefig(OUT / 'server_coloc_timing.png', dpi=130); plt.close(fig)
print('wrote', OUT / 'server_avoidable_reads.png', OUT / 'server_coloc_timing.png')
