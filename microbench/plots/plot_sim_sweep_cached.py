#!/usr/bin/env python3
"""N2 with the node cache (CACHE_NODES=100000, SEEDS="1 2 3"): same look as plots/plot_sim_sweep.py, mean over seeds.
Writes <out_prefix>_qps.png and <out_prefix>_latency.png; prints the gains (mean, seed min/max) and, if an uncached
sweep dir is given, the uncached gains side by side. Several cached dirs are merged (rows keyed by config, policy, seed);
configs/policies are drawn in the fixed order below when present (W16 and avoid3 appear once they have been run).
Usage: plots/plot_sim_sweep_cached.py <out_prefix> <cached_dir>[,<cached_dir>...] [uncached_sweep_dir]
"""
import collections, csv, sys
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams['font.family'] = ['DejaVu Sans']
INK, MUTED, GRID = '#333333', '#8a8a8a', '#e6e6e6'
ALL_CFG = ['T8_W4', 'T16_W4', 'T32_W8', 'T48_W8', 'T32_W16', 'T48_W16']
ALL_POL = ['avoid2', 'avoid2_wait35', 'avoid3', 'hint2_pf4k', 'avoid2_pf4k']
LABEL = {'avoid2': 'avoid (nominate 2W)', 'avoid2_wait35': 'avoid 2W, wait if < 35 µs', 'avoid3': 'avoid (nominate 3W)',
         'hint2_pf4k': 'prefetch only (no avoid)', 'avoid2_pf4k': 'avoid 2W + prefetch'}
COLOR = {'avoid2': '#6f9bc8', 'avoid2_wait35': '#b3cde6', 'avoid3': '#86bfa0', 'hint2_pf4k': '#e6bd6b', 'avoid2_pf4k': '#c97f94'}


def load(dirs):
    rows = {}
    for d in dirs:
        for r in csv.DictReader(open(Path(d) / 'summary.csv')):
            rows[(r['config'], r['policy'], r.get('seed', ''))] = r  # later dirs override duplicates
    g = collections.defaultdict(lambda: collections.defaultdict(list))
    for (c, p, s) in sorted(rows):
        for k in ('qps', 'latency_mean_us'):
            g[(c, p)][k].append(float(rows[(c, p, s)][k]))
    return g


def gain(g, c, p, key):
    """percent gain per seed (QPS up, latency down are positive), pairing seeds in order"""
    b, v = g[(c, 'base')][key], g[(c, p)][key]
    s = 1 if key == 'qps' else -1
    return [s * 100 * (x / y - 1) for x, y in zip(v, b)] if len(b) == len(v) else [s * 100 * (np.mean(v) / np.mean(b) - 1)]


def main():
    pre, dirs = sys.argv[1], [Path(x).resolve() for x in sys.argv[2].split(',')]
    g = load(dirs)
    old = load([sys.argv[3]]) if len(sys.argv) > 3 else None
    CFG = [c for c in ALL_CFG if (c, 'base') in g]
    POL = [p for p in ALL_POL if all((c, p) in g for c in CFG)]
    XLAB = [c.replace('_', ' ') for c in CFG]
    x = np.arange(len(CFG)); bw = 0.8 / len(POL)
    note = f'N2 simulator ({dirs[0].name} and follow-ups). SIFT100M, L100, 10,000 queries, node cache 100k nodes, mean of 3 seeds, simulated time.\n' \
           'Shaded: low load (W4). At T32+ the real run has shared congestion the model lacks; high-load gains are model-only.'
    for key, fname, title in [('qps', 'qps', 'QPS change vs original, node cache on (%, higher is better)'),
                              ('latency_mean_us', 'latency', 'Mean query latency reduction vs original, node cache on (%, higher is better)')]:
        print(f'\n{key}: config policy cached_mean [min max]' + ('  uncached' if old else ''))
        fig, ax = plt.subplots(figsize=(10, 4.6))
        ax.axvspan(-0.5, 1.5, color='#f5f7fa', zorder=0)
        for j, p in enumerate(POL):
            vals = []
            for c in CFG:
                gs = gain(g, c, p, key); vals.append(float(np.mean(gs)))
                extra = f'  {np.mean(gain(old, c, p, key)):+.1f}' if old and (c, p) in old else ''
                print(f'{c} {p} {np.mean(gs):+.1f} [{min(gs):+.1f} {max(gs):+.1f}]{extra}')
            xo = x + (j - (len(POL) - 1) / 2) * bw
            ax.bar(xo, vals, bw * 0.88, color=COLOR[p], label=LABEL[p])
            for xi, v in zip(xo, vals):
                ax.text(xi, v + 0.5, f'{v:.0f}', ha='center', va='bottom', fontsize=7.5, color=MUTED)
        ax.axhline(0, color='#999999', lw=0.8)
        ax.set_ylim(0, max(45, 5 * np.ceil(ax.get_ylim()[1] / 5) + 3))
        ax.set_xticks(x); ax.set_xticklabels(XLAB, fontsize=10, color=INK)
        for s in ('top', 'right', 'left'): ax.spines[s].set_visible(False)
        ax.spines['bottom'].set_color('#bbbbbb')
        ax.tick_params(axis='y', length=0, colors=MUTED, labelsize=9); ax.tick_params(axis='x', length=0)
        ax.grid(axis='y', color=GRID, lw=0.8); ax.set_axisbelow(True)
        ax.set_ylabel('QPS gain (%)' if key == 'qps' else 'latency reduction (%)', color=INK)
        ax.text(0.5, ax.get_ylim()[1] - 1, 'low load', ha='center', va='top', fontsize=8.5, color=MUTED)
        ax.legend(frameon=False, fontsize=8.5, ncol=len(POL), loc='lower left', bbox_to_anchor=(0, 1.0), handlelength=1.1,
                  columnspacing=1.0)
        ax.set_title(title, loc='left', fontsize=12.5, color=INK, pad=34)
        fig.text(0.01, 0.015, note, fontsize=8, color=MUTED)
        fig.tight_layout(rect=(0, 0.07, 1, 1)); fig.savefig(f'{pre}_{fname}.png', dpi=150); plt.close(fig)
        print('->', f'{pre}_{fname}.png')


if __name__ == '__main__':
    main()
