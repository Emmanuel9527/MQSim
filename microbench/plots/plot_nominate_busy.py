#!/usr/bin/env python3
"""N1 talk figure: only "rounds with a busy-unit read" for W=4 and W=8, side by side.
Lines: nominate k (one-hue ramp, light = k = W = original). The p = 0.3 column (about the T16 W4 plane load) is marked.
Usage: plots/plot_nominate_busy.py <sweep_dir> <out.png>
"""
import csv, sys
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams['font.family'] = ['DejaVu Sans']
RAMP = ['#9cc3ef', '#3987e5', '#1c5cab', '#0d366b']
INK, MUTED = '#333333', '#888888'


def main():
    d = Path(sys.argv[1]).resolve()
    rows = list(csv.DictReader(open(d / 'summary.csv')))
    Ws = sorted({int(r['W']) for r in rows})
    fig, ax = plt.subplots(1, len(Ws), figsize=(5.6 * len(Ws), 4.2), sharey=True)
    for a, W in zip(ax, Ws):
        rw = [r for r in rows if int(r['W']) == W]
        a.axvline(0.3, color='#dddddd', lw=6, zorder=0)
        a.text(0.3, 101, 'p = 0.3 (~T16 W4 load)', ha='center', va='bottom', fontsize=8.5, color=MUTED)
        for j, k in enumerate(sorted({int(r['k']) for r in rw})):
            rk = sorted((r for r in rw if int(r['k']) == k), key=lambda r: float(r['p']))
            p = [float(r['p']) for r in rk]; v = [float(r['busy_round_pct']) for r in rk]
            lab = f'k={k}' + (' (original)' if k == W else f' ({k / W:g}W)')
            a.plot(p, v, color=RAMP[j], marker='o', ms=5, lw=2.2 if k in (W, 2 * W) else 1.5, label=lab)
            if k in (W, 2 * W):
                y = v[p.index(0.3)]
                a.annotate(f'{y:.0f}%', (0.3, y), xytext=(-8, 6 if k == W else 8), textcoords='offset points',
                           ha='right', fontsize=10, fontweight='bold', color=INK)
        a.set_title(f'W = {W}', loc='left', fontsize=12, color=INK)
        a.set_xlabel('unit busy probability p', color=INK)
        a.set_xticks([0, 0.1, 0.2, 0.3, 0.4]); a.set_ylim(-3, 108)
        a.grid(axis='y', alpha=0.25)
        for s in ('top', 'right'): a.spines[s].set_visible(False)
        a.legend(frameon=False, fontsize=9, loc='upper left', bbox_to_anchor=(0, 0.93))
    ax[0].set_ylabel('rounds with a busy-unit read (%)', color=INK)
    fig.suptitle('Host nominates k, SSD picks W on free units: fewer rounds hit a busy plane', x=0.01, ha='left',
                 fontsize=12.5, color=INK)
    fig.tight_layout(); fig.savefig(sys.argv[2], dpi=150); print(sys.argv[2])


if __name__ == '__main__':
    main()
