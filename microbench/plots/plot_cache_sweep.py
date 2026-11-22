#!/usr/bin/env python3
"""Node-cache sweep on the real SSD (server_sweeps/diskann_cache_sweep.sh) against the ladder simulator's prediction.
Usage: plots/plot_cache_sweep.py <cache_sweep_dir> <ladder_results_dir> <out.png>
Real: num_nodes_to_cache = 1k..1M nodes (BFS levels from the medoid). Simulator: the N hottest sectors of other
queries (sweep_D.jsonl, 'hostcache N'); paired as 10 nodes = 1 sector. Prints the table it plots.
"""
import collections, csv, sys
from pathlib import Path
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ladder'))  # ladder_table
from ladder_table import load

real_dir, sim_dir, out = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
plt.rcParams['font.family'] = ['Noto Sans CJK JP', 'DejaVu Sans']
BLUE, ORANGE, INK = '#2a78d6', '#eb6834', '#33332f'
acc = collections.defaultdict(list)
for r in csv.DictReader(open(real_dir / 'summary.csv')):
    acc[(r['config'], int(r['cache_nodes']))].append((float(r['qps']), float(r['mean_cache_hits'])))
D = load(sim_dir / 'sweep_D.jsonl')
cfgs = ['T8_W4', 'T16_W4', 'T32_W8', 'T48_W8']; sizes = [1000, 10000, 100000, 1000000]
fig, axs = plt.subplots(1, 4, figsize=(16, 4.6), sharey=True)
print('config cache_nodes real_qps real_gain% real_hits sim_gain% sim_hits')
for ax, c in zip(axs, cfgs):
    b = sum(q for q, _ in acc[(c, 0)]) / len(acc[(c, 0)]); sb = D[(c, 'base')]['qps']
    rg = [100 * (sum(q for q, _ in acc[(c, n)]) / len(acc[(c, n)]) / b - 1) for n in sizes]
    sg = [100 * (D[(c, f'hostcache {n // 10}')]['qps'] / sb - 1) for n in sizes]
    for n, a, s in zip(sizes, rg, sg):
        print(c, n, f"{sum(q for q, _ in acc[(c, n)]) / len(acc[(c, n)]):.0f}", f'{a:+.1f}', acc[(c, n)][0][1], f'{s:+.1f}',
              f"{D[(c, f'hostcache {n // 10}')]['host_cached_per_query']:.1f}")
    x = range(len(sizes))
    ax.plot(x, rg, color=BLUE, marker='o', ms=8, lw=2, label='真機 T500（節點快取）')
    ax.plot(x, sg, color=ORANGE, marker='s', ms=8, lw=2, ls='--', label='模擬預測（熱點 sector）')
    for i in x:
        hi = rg[i] >= sg[i]
        ax.annotate(f'{rg[i]:+.0f}%', (i, rg[i]), textcoords='offset points', xytext=(0, 9 if hi else -15), ha='center', color=INK, fontsize=10)
        ax.annotate(f'{sg[i]:+.0f}%', (i, sg[i]), textcoords='offset points', xytext=(0, -15 if hi else 9), ha='center', color=INK, fontsize=10)
    ax.set_xticks(list(x)); ax.set_xticklabels(['1 千', '1 萬', '10 萬', '100 萬']); ax.set_xlabel('快取節點數')
    ax.set_title(f"{c.replace('_', ' ')}（不開快取 {b:,.0f} QPS）", loc='left', fontsize=11); ax.grid(alpha=0.25); ax.set_ylim(0, 55)
    for s in ('top', 'right'): ax.spines[s].set_visible(False)
axs[0].set_ylabel('QPS 相對不開快取（%）'); axs[0].legend(frameon=False, loc='upper left', fontsize=10)
fig.suptitle('節點快取的效果：真機 T500 對照模擬預測（SIFT100M，L100）', x=0.01, ha='left', fontsize=13)
fig.tight_layout(rect=(0, 0, 1, 0.94)); fig.savefig(out, dpi=130)
