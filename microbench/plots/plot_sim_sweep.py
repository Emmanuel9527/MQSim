#!/usr/bin/env python3
"""N2: plot server_sweeps/diskann_sim_sweep.sh summary.csv as three separate figures (same config order and colours):
  <out>_validation.png  original-policy QPS: real T500 run, MQSim closed-loop replay, this simulator
  <out>_qps.png         QPS change vs the original policy, main policies per (T, W)
  <out>_latency.png     mean query latency reduction vs the original policy (positive = faster)
Usage: plots/plot_sim_sweep.py <sweep_dir> [out_prefix]   (default out_prefix: <sweep_dir>/sim_sweep)
Also writes <sweep_dir>/sim_sweep.json.
"""
import csv, json, sys
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

plt.rcParams['font.family'] = ['DejaVu Sans']
INK, MUTED, GRID = '#333333', '#8a8a8a', '#e6e6e6'
CFG = ['T8_W4', 'T16_W4', 'T32_W8', 'T48_W8', 'T32_W16', 'T48_W16']
MQSIM = {'T8_W4': 2035, 'T16_W4': 3426, 'T32_W8': 5244, 'T48_W8': 5498, 'T32_W16': 3858, 'T48_W16': 3854}
REAL = {'T8_W4': 2035, 'T16_W4': 3439, 'T32_W8': 4770, 'T48_W8': 4936, 'T32_W16': 3636, 'T48_W16': 4033}
POL = ['avoid2', 'avoid2_wait35', 'avoid3', 'hint2_pf4k', 'avoid2_pf4k']
LABEL = {'avoid2': 'avoid (nominate 2W)', 'avoid2_wait35': 'avoid 2W, wait if < 35 µs', 'avoid3': 'avoid (nominate 3W)',
         'hint2_pf4k': 'prefetch only (no avoid)', 'avoid2_pf4k': 'avoid 2W + prefetch'}
COLOR = dict(zip(POL, ['#6f9bc8', '#b3cde6', '#86bfa0', '#e6bd6b', '#c97f94']))
XLAB = [c.replace('_', ' ') for c in CFG]


def style(ax, x):
    ax.set_xticks(x); ax.set_xticklabels(XLAB, fontsize=10, color=INK)
    for s in ('top', 'right', 'left'):
        ax.spines[s].set_visible(False)
    ax.spines['bottom'].set_color('#bbbbbb')
    ax.tick_params(axis='y', length=0, colors=MUTED, labelsize=9); ax.tick_params(axis='x', length=0)
    ax.grid(axis='y', color=GRID, lw=0.8); ax.set_axisbelow(True)
    # light band separating low load (W4) from high load
    ax.axvspan(-0.5, 1.5, color='#f5f7fa', zorder=0)


def finish(fig, ax, title, note, out):
    ax.set_title(title, loc='left', fontsize=12.5, color=INK, pad=34)
    fig.text(0.01, 0.015, note, fontsize=8, color=MUTED)
    fig.subplots_adjust(left=0.08, right=0.99, bottom=0.19, top=0.80)
    lo, hi = ax.get_ylim()
    top = hi > 2  # bars go up: label at the top of the band; bars go down: at the bottom
    ax.text(0.5, hi if top else lo, 'low load', ha='center', va='top' if top else 'bottom', fontsize=8.5, color=MUTED)
    fig.savefig(out, dpi=150); plt.close(fig); print(out)


def main():
    d = Path(sys.argv[1]).resolve()
    pre = sys.argv[2] if len(sys.argv) > 2 else str(d / 'sim_sweep')
    rows = {(r['config'], r['policy']): r for r in csv.DictReader(open(d / 'summary.csv'))}
    x = np.arange(len(CFG))
    note = f'{d.name}. SIFT100M, L100, 10,000 queries, simulated time. Shaded: low load (W4).\n' \
           'At T32+ the real run has shared congestion the model lacks; high-load gains are model-only.'

    # 1) validation
    fig, ax = plt.subplots(figsize=(10, 4.6)); style(ax, x)
    sim = [float(rows[(c, 'base')]['qps']) for c in CFG]
    bw = 0.26
    series = [('real T500', [REAL[c] for c in CFG], '#cfcfcf'), ('MQSim closed loop', [MQSIM[c] for c in CFG], '#8fb0d3'),
              ('this simulator', sim, '#e3a26f')]
    for i, (lab, vals, col) in enumerate(series):
        ax.bar(x + (i - 1) * bw, vals, bw * 0.9, color=col, label=lab)
    for i, c in enumerate(CFG):
        ax.text(x[i] + bw, sim[i] + 60, f'{100 * (sim[i] / MQSIM[c] - 1):+.1f}%', ha='center', va='bottom',
                fontsize=8.5, color=INK)
    ax.set_ylabel('QPS', color=INK)
    ax.legend(frameon=False, fontsize=9, ncol=3, loc='lower left', bbox_to_anchor=(0, 1.0), handlelength=1.2)
    finish(fig, ax, 'Simulator vs MQSim: original-policy QPS (label: vs MQSim)', note, f'{pre}_validation.png')

    # 2) and 3) gains per policy
    out = {}
    bw = 0.155
    for key, fname, title, sign in [
            ('qps', 'qps', 'QPS change vs original (%, higher is better)', 1),
            ('latency_mean_us', 'latency', 'Mean query latency reduction vs original (%, higher is better)', -1)]:
        fig, ax = plt.subplots(figsize=(10, 4.6)); style(ax, x)
        for j, p in enumerate(POL):
            vals = [100 * (float(rows[(c, p)][key]) / float(rows[(c, 'base')][key]) - 1) for c in CFG]
            for c, v in zip(CFG, vals):
                out.setdefault(p, {}).setdefault(c, {})['qps_gain_pct' if sign > 0 else 'latency_change_pct'] = v
            if sign < 0:  # plot latency as a reduction so that bars point up and higher is better
                vals = [-v for v in vals]
            xo = x + (j - (len(POL) - 1) / 2) * bw
            ax.bar(xo, vals, bw * 0.88, color=COLOR[p], label=LABEL[p])
            for xi, v in zip(xo, vals):
                ax.text(xi, v + (0.6 if v >= 0 else -0.6), f'{v:.0f}', ha='center', va='bottom' if v >= 0 else 'top',
                        fontsize=7, color=MUTED)
        ax.axhline(0, color='#999999', lw=0.8)
        lo, hi = ax.get_ylim(); ax.set_ylim(lo - 3 if lo < -1 else lo, hi + 3 if hi > 1 else 1.5)
        ax.set_ylabel('QPS gain (%)' if sign > 0 else 'latency reduction (%)', color=INK)
        ax.legend(frameon=False, fontsize=8.5, ncol=5, loc='lower left', bbox_to_anchor=(0, 1.0), handlelength=1.1,
                  columnspacing=1.0)
        finish(fig, ax, title, note, f'{pre}_{fname}.png')
    (d / 'sim_sweep.json').write_text(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
