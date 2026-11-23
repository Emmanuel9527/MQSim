#!/usr/bin/env python3
"""Real T500 load sweep (reports/2026-11-15_diskann_inssd_study.md, 2026-11-15 負載掃描; no node cache): IOPS / QPS / mean latency
against mean in-flight reads. Usage: plots/plot_load_scaling.py <out.png>
"""
import sys
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams['font.family'] = ['Noto Sans CJK JP', 'DejaVu Sans']
BLUE, INK, MUTED, GRID = '#2a78d6', '#33332f', '#8a8984', '#ecebe7'
#        config     in-flight  IOPS(萬)  QPS    latency µs
rows = [('T8 W4',    21.1,   24.1, 2035,  87.7),
        ('T16 W4',   40.2,   40.7, 3439,  98.9),
        ('T32 W8',  125.2,   64.3, 4770, 194.9),
        ('T48 W8',  194.4,   66.5, 4936, 292.4),
        ('T48 W16', 395.9,   68.0, 4032, 582.0)]
x = [r[1] for r in rows]
panels = [('IOPS（萬）', [r[2] for r in rows], '{:.0f} 萬', (0, 80)),
          ('QPS', [r[3] for r in rows], '{:,.0f}', (0, 6000)),
          ('平均延遲（µs）', [r[4] for r in rows], '{:.0f}', (0, 650))]
# label offsets (points) per panel and config, to keep labels clear of the line
off = [[(6, -12), (6, -12), (-8, 8), (0, 8), (0, 8)],
       [(6, -12), (6, -12), (-8, 8), (0, 8), (0, -16)],
       [(-6, 8), (8, -4), (8, -4), (8, -4), (-10, -1)]]

fig, axs = plt.subplots(1, 3, figsize=(14, 4.3))
for k, (ax, (title, y, fmt, ylim)) in enumerate(zip(axs, panels)):
    ax.axvspan(100, 420, color='#f6f5f2', zorder=0)
    ax.plot(x, y, color=BLUE, lw=2, zorder=2)
    ax.scatter(x, y, s=46, color=BLUE, edgecolor='white', linewidth=2, zorder=3)
    for (cfg, *_), xi, yi, (dx, dy) in zip(rows, x, y, off[k]):
        ha = 'left' if dx > 0 else ('right' if dx < 0 else 'center')
        ax.annotate(f'{cfg}\n{fmt.format(yi)}', (xi, yi), xytext=(dx, dy), textcoords='offset points', ha=ha,
                    va='bottom' if dy > 0 else ('top' if dy < -2 else 'center'), fontsize=8.5, color=INK, linespacing=1.1)
    ax.set_title(title, loc='left', fontsize=12, color=INK, pad=10)
    ax.set_xlim(0, 420); ax.set_ylim(*ylim)
    ax.set_xlabel('平均在途讀取（筆）', color=MUTED, fontsize=9.5)
    ax.grid(axis='y', color=GRID, lw=1); ax.set_axisbelow(True)
    for s in ('top', 'right', 'left'): ax.spines[s].set_visible(False)
    ax.spines['bottom'].set_color(MUTED); ax.tick_params(colors=MUTED, labelsize=9, length=0)
    ax.text(410, ylim[1] * 0.03, '飽和區', ha='right', va='bottom', fontsize=9, color=MUTED)
fig.suptitle('T500 真機：同時跑的 query 加多後，IOPS 停在約 65 萬，只剩延遲上升', x=0.01, ha='left',
             fontsize=13.5, color=INK)
fig.text(0.01, 0.005, 'SIFT100M R64，L100，K10，不開 node cache。T = 同時執行的 query 數（thread），W = beamwidth。',
         fontsize=8.5, color=MUTED)
fig.tight_layout(rect=(0, 0.03, 1, 1)); fig.savefig(sys.argv[1], dpi=160); print('->', sys.argv[1])
