#!/usr/bin/env python3
"""Plot server_sweeps/analyze_prediction_by_round.py output: share of next-round reads that are old host candidates vs new
neighbours, per round index r, one panel per W (side by side).
Usage: plots/plot_prediction_by_round.py <out.png> <by_round_W4.json> <by_round_W8.json> ...
"""
import json, sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams['font.family'] = ['DejaVu Sans']
INK, MUTED = '#333333', '#888888'
OLD, NEW = '#6f9bc8', '#e6bd6b'


def main():
    out, files = sys.argv[1], sys.argv[2:]
    res = [json.load(open(f)) for f in files]
    fig, ax = plt.subplots(1, len(res), figsize=(5.8 * len(res), 4.2), sharey=True, squeeze=False)
    for a, r in zip(ax[0], res):
        rows = [b for b in r['by_round'] if b['rounds'] >= 0.5 * r['queries']]   # rounds most queries reach
        x = [b['r'] for b in rows]; old = [b['old_pct'] for b in rows]
        a.fill_between(x, 0, old, color=OLD, step='mid', lw=0, label='old host candidate (host can hint)')
        a.fill_between(x, old, 100, color=NEW, step='mid', lw=0, label="new neighbour of round r (host can't know)")
        a.axhline(r['overall_old_pct'], color=INK, lw=1, ls=(0, (3, 3)))
        a.text(x[-1], r['overall_old_pct'] + 2, f"average {r['overall_old_pct']:.0f}%", ha='right', va='bottom',
               fontsize=9, color=INK)
        a.set_title(f"W = {r['W']}  (mean {r['rounds_mean']:.1f} rounds/query)", loc='left', fontsize=12, color=INK)
        a.set_xlabel('round r (predicting the reads of round r+1)', color=INK)
        a.set_xlim(x[0] - 0.5, x[-1] + 0.5); a.set_ylim(0, 100)
        for s in ('top', 'right'): a.spines[s].set_visible(False)
        a.tick_params(colors=MUTED)
    ax[0][0].set_ylabel('share of round r+1 reads (%)', color=INK)
    h, l = ax[0][0].get_legend_handles_labels()
    fig.legend(h, l, loc='upper left', ncol=2, frameon=False, fontsize=9.5, bbox_to_anchor=(0.01, 0.93))
    fig.suptitle('Where the next round\'s reads come from, over the course of a search', x=0.01, ha='left',
                 fontsize=12.5, color=INK)
    fig.text(0.01, 0.01, 'SIFT100M, L100, 1,000 queries, original policy (offline replay, 0 mismatches). '
             'Rounds reached by fewer than half the queries are omitted.', fontsize=8, color=MUTED)
    fig.tight_layout(rect=(0, 0.04, 1, 0.89)); fig.savefig(out, dpi=150); print(out)


if __name__ == '__main__':
    main()
