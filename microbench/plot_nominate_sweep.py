#!/usr/bin/env python3
"""N1: plot diskann_nominate_sweep.sh summary.csv. Rows: W; columns: rounds with a busy-unit read, hop change vs the
original beam (k = W), recall@10, prefetch hit fraction. Lines: nominate k (one-hue ramp, light = k = W).
Usage: plot_nominate_sweep.py <sweep_dir>   -> <sweep_dir>/nominate_sweep.png
"""
import csv, sys
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams['font.family'] = ['Noto Sans CJK TC', 'Noto Sans CJK JP', 'DejaVu Sans']
RAMP = ['#86b6ef', '#3987e5', '#1c5cab', '#0d366b']


def main():
    d = Path(sys.argv[1]).resolve()
    rows = list(csv.DictReader(open(d / 'summary.csv')))
    Ws = sorted({int(r['W']) for r in rows})
    fig, ax = plt.subplots(len(Ws), 4, figsize=(18, 4.3 * len(Ws)), squeeze=False)
    for i, W in enumerate(Ws):
        rw = [r for r in rows if int(r['W']) == W]
        ks = sorted({int(r['k']) for r in rw})
        base_hops = {float(r['p']): float(r['mean_hops']) for r in rw if int(r['k']) == W}
        for j, k in enumerate(ks):
            rk = sorted((r for r in rw if int(r['k']) == k), key=lambda r: float(r['p']))
            p = [float(r['p']) for r in rk]
            kw = dict(color=RAMP[j % len(RAMP)], marker='o', ms=6, lw=2, label=f'k={k}' + ('（原始）' if k == W else f'（{k / W:g}W）'))
            ax[i, 0].plot(p, [float(r['busy_round_pct']) for r in rk], **kw)
            ax[i, 1].plot(p, [100 * (float(r['mean_hops']) / base_hops[float(r['p'])] - 1) for r in rk], **kw)
            ax[i, 2].plot(p, [float(r['recall']) for r in rk], **kw)
            if k != W:
                ax[i, 3].plot(p, [float(r['prefetch_hit_pct']) for r in rk], **kw)
        titles = ['有讀取落在忙碌單位的輪（%）', 'hop 相對原始的變化（%）', 'recall@10（%）', '預讀命中（佔全部讀取 %）']
        for c in range(4):
            a = ax[i, c]
            a.set_title(f'W={W}：{titles[c]}', loc='left', fontsize=11)
            a.set_xlabel('單位忙碌機率 p'); a.grid(alpha=0.25)
            for s in ('top', 'right'): a.spines[s].set_visible(False)
        ax[i, 0].legend(frameon=False, fontsize=9)
    fig.text(0.01, 0.005, f'{d.name}。功能模型：每輪每個單位以機率 p 忙碌（獨立）。k＝主機提名數，SSD 從中挑 W 個非忙碌單位上的候選；'
             '預讀＝提名未選者進入每 query 256 sector 的緩衝。', fontsize=9, color='#555')
    fig.tight_layout(rect=(0, 0.025, 1, 1)); fig.savefig(d / 'nominate_sweep.png', dpi=125); print(d / 'nominate_sweep.png')


if __name__ == '__main__':
    main()
