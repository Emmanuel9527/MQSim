#!/usr/bin/env python3
"""N1: plot server_sweeps/diskann_nominate_sweep.sh summary.csv. Rows: W; columns: rounds with a busy-unit read, hop change vs the
original beam (k = W), recall@10, prefetch hit fraction. Lines: nominate k (one-hue ramp, light = k = W).
Usage: plots/plot_nominate_sweep.py <sweep_dir> [--brief]   -> <sweep_dir>/nominate_sweep.png
  --brief: only the first two columns (busy rounds, hop change) -> <sweep_dir>/nominate_sweep_brief.png
"""
import csv, sys
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams['font.family'] = ['DejaVu Sans']
RAMP = ['#86b6ef', '#3987e5', '#1c5cab', '#0d366b']


def main():
    d = Path(sys.argv[1]).resolve()
    brief = '--brief' in sys.argv[2:]
    nc = 2 if brief else 4
    rows = list(csv.DictReader(open(d / 'summary.csv')))
    Ws = sorted({int(r['W']) for r in rows})
    fig, ax = plt.subplots(len(Ws), nc, figsize=(4.5 * nc + (1 if brief else 0), 4.3 * len(Ws)), squeeze=False)
    for i, W in enumerate(Ws):
        rw = [r for r in rows if int(r['W']) == W]
        ks = sorted({int(r['k']) for r in rw})
        base_hops = {float(r['p']): float(r['mean_hops']) for r in rw if int(r['k']) == W}
        for j, k in enumerate(ks):
            rk = sorted((r for r in rw if int(r['k']) == k), key=lambda r: float(r['p']))
            p = [float(r['p']) for r in rk]
            kw = dict(color=RAMP[j % len(RAMP)], marker='o', ms=6, lw=2, label=f'k={k}' + (' (original)' if k == W else f' ({k / W:g}W)'))
            ax[i, 0].plot(p, [float(r['busy_round_pct']) for r in rk], **kw)
            ax[i, 1].plot(p, [100 * (float(r['mean_hops']) / base_hops[float(r['p'])] - 1) for r in rk], **kw)
            if brief:
                continue
            ax[i, 2].plot(p, [float(r['recall']) for r in rk], **kw)
            if k != W:
                ax[i, 3].plot(p, [float(r['prefetch_hit_pct']) for r in rk], **kw)
        titles = ['rounds with a busy-unit read (%)', 'hop change vs original (%)', 'recall@10 (%)', 'prefetch hits (% of all reads)']
        for c in range(nc):
            a = ax[i, c]
            a.set_title(f'W={W}: {titles[c]}', loc='left', fontsize=11)
            a.set_xlabel('unit busy probability p'); a.grid(alpha=0.25)
            for s in ('top', 'right'): a.spines[s].set_visible(False)
        ax[i, 0].legend(frameon=False, fontsize=9)
    note = '' if brief else ' Prefetch = unchosen nominees go into a 256-sector per-query buffer.'
    fig.text(0.01, 0.005, f'{d.name}. Functional model: each unit is busy with probability p per round (independent).' + ('\n' if brief else ' ') + 'k = host nominations; '
             'the SSD picks W candidates on free units.' + note, fontsize=9, color='#555')
    fig.tight_layout(rect=(0, 0.05 if brief else 0.025, 1, 1)); out = d / ('nominate_sweep_brief.png' if brief else 'nominate_sweep.png')
    fig.savefig(out, dpi=125); print(out)


if __name__ == '__main__':
    main()
