#!/usr/bin/env python3
"""N2 policy gains without vs with the node cache.
Usage: plots/plot_sim_sweep_cache.py <uncached sweep dir> <cached sweep dir> <out.png>
Uncached: server_sweeps/diskann_sim_sweep.sh, seed 1 (no SEEDS). Cached: CACHE_NODES=100000 SEEDS="1 2 3" (mean over seeds; the
seed spread is printed). Gains are relative to the base policy of the same sweep (same cache setting).
"""
import collections, csv, sys
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt

old_dir, new_dir, out = sys.argv[1:4]
plt.rcParams['font.family'] = ['Noto Sans CJK JP', 'DejaVu Sans']
BLUE, ORANGE, INK = '#2a78d6', '#eb6834', '#33332f'
CFGS = ['T8_W4', 'T16_W4', 'T32_W8', 'T48_W8']
POLS = [('avoid2', '避開 2W'), ('avoid2_wait35', '避開＋短等待'), ('hint2_pf4k', '只預讀'), ('avoid2_pf4k', '避開＋預讀')]


def load(d):
    g = collections.defaultdict(list)
    for r in csv.DictReader(open(f'{d}/summary.csv')): g[(r['config'], r['policy'])].append(float(r['qps']))
    return {k: sum(v) / len(v) for k, v in g.items()}, g


old, _ = load(old_dir); new, newall = load(new_dir)
fig, axs = plt.subplots(1, 4, figsize=(16, 4.8), sharey=True)
for ax, c in zip(axs, CFGS):
    xs = range(len(POLS))
    og = [100 * (old[(c, p)] / old[(c, 'base')] - 1) for p, _ in POLS]
    ng = [100 * (new[(c, p)] / new[(c, 'base')] - 1) for p, _ in POLS]
    for i, (o, n) in enumerate(zip(og, ng)):
        ax.bar(i - 0.2, o, 0.38, color=BLUE, edgecolor='white', linewidth=1)
        ax.bar(i + 0.2, n, 0.38, color=ORANGE, edgecolor='white', linewidth=1)
        ax.text(i - 0.2, o + 0.8, f'{o:+.0f}', ha='center', va='bottom', fontsize=9.5, color=INK)
        ax.text(i + 0.2, n + 0.8, f'{n:+.0f}', ha='center', va='bottom', fontsize=9.5, color=INK)
        p = POLS[i][0]; q = newall[(c, p)]; b = sum(newall[(c, 'base')]) / len(newall[(c, 'base')])
        print(c, p, f'old {o:+.1f}% new {n:+.1f}% (seeds {100 * (min(q) / b - 1):+.1f}..{100 * (max(q) / b - 1):+.1f})')
    ax.set_xticks(list(xs)); ax.set_xticklabels([l for _, l in POLS], fontsize=10)
    tag = '低負載，模型已驗證' if 'W4' in c else '高負載，僅模型內比較'
    ax.set_title(f"{c.replace('_', ' ')}（{tag}）", loc='left', fontsize=11)
    ax.grid(axis='y', alpha=0.25); ax.set_axisbelow(True); ax.set_ylim(0, 50)
    for s in ('top', 'right'): ax.spines[s].set_visible(False)
axs[0].set_ylabel('QPS 相對同條件的原始策略（%）')
h = [plt.Rectangle((0, 0), 1, 1, color=BLUE), plt.Rectangle((0, 0), 1, 1, color=ORANGE)]
fig.legend(h, ['不開快取（11/15，種子 1）', '節點快取 10 萬（3 個種子平均）'], loc='lower center', ncol=2, frameon=False, fontsize=11)
fig.suptitle('N2：開節點快取後，避開與預讀的收益還剩多少（SIFT100M，L100）', x=0.01, ha='left', fontsize=13)
fig.tight_layout(rect=(0, 0.07, 1, 0.94)); fig.savefig(out, dpi=130)
