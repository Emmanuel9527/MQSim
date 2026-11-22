#!/usr/bin/env python3
"""Barrier vs asynchronous expansion inside the SSD, and per-hop latency of controller-level vs in-plane compute.

Idealised: one query, W=4 reads in flight, flash read = 75 us, ~30% of reads hit a busy plane and wait an extra
10-60 us (T16 W4 average queueing is about 50 us on the slowest read of a round). Same latency sequence in both panels.
Usage: plots/plot_barrier_vs_async.py <out.png>
"""
import sys
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

plt.rcParams['font.family'] = ['Noto Sans CJK TC', 'Noto Sans CJK JP', 'DejaVu Sans']
W, READ, CPU, T_END = 4, 75.0, 3.0, 620
rng = np.random.default_rng(7)
LAT = [READ + (rng.uniform(10, 60) if rng.random() < 0.3 else 0) for _ in range(200)]
C = dict(read='#c9ccd0', queue='#d1495b', cpu='#3d5a80', idle='#ffffff')


def lane_bar(ax, lane, s, e, color, h=0.7):
    ax.broken_barh([(s, e - s)], (lane - h / 2, h), facecolors=color, edgecolors='none')


def draw_read(ax, lane, s, lat):
    lane_bar(ax, lane, s, s + READ, C['read'])
    lane_bar(ax, lane, s + READ, s + lat, C['queue'])


def style(ax, title, note):
    ax.set_title(title, loc='left', fontsize=11, fontweight='bold')
    ax.set_yticks(range(W)); ax.set_yticklabels([f'在途讀取 {i + 1}' for i in range(W)], fontsize=9)
    ax.set_xlim(0, T_END); ax.set_ylim(-0.6, W + 0.2)
    for s in ('top', 'right', 'left'): ax.spines[s].set_visible(False)
    ax.tick_params(axis='y', length=0)
    ax.text(T_END - 2, W - 0.35, note, ha='right', va='bottom', fontsize=9, color='#333')


def barrier(ax):
    t, k, n = 0.0, 0, 0
    while True:
        lats = LAT[k:k + W]
        if t + max(lats) > T_END: break
        for i, L in enumerate(lats):
            draw_read(ax, i, t, L)
        end = t + max(lats)
        lane_bar(ax, W - 0.5, end, end + CPU, C['cpu'], h=4.6) if False else None
        for i in range(W): lane_bar(ax, i, end, end + CPU, C['cpu'])
        ax.axvline(end + CPU, color='#999', ls=':', lw=0.8)
        k += W; n += W; t = end + CPU
    style(ax, '(a) SSD 內走訪、保留 barrier：每輪等最慢那筆', f'{T_END}µs 內完成 {n} 筆（紅色 = 撞車排隊；空白 = 等最慢那筆）')
    return n


def async_(ax):
    nxt = [0.0] * W; k = 0; n = 0
    while True:
        i = int(np.argmin(nxt)); s = nxt[i]; L = LAT[k]
        if s + L > T_END: break
        draw_read(ax, i, s, L); lane_bar(ax, i, s + L, s + L + CPU, C['cpu'])
        nxt[i] = s + L + CPU; k += 1; n += 1
    style(ax, '(b) SSD 內非同步走訪：任一筆回來就展開、補下一筆', f'{T_END}µs 內完成 {n} 筆（同一串延遲；沒有等待空白）')
    return n


def per_hop(ax):
    rows = [('控制器層計算', [('tR', 70.0, C['read']), ('指令＋傳出', 2.0, '#14808a'), ('控制器計算', CPU, C['cpu'])]),
            ('plane 內計算', [('tR', 70.0, C['read']), ('plane 內計算', CPU, '#6d597a'), ('轉送到別的 plane', 0.3, '#1a1a1a')])]
    for y, (name, parts) in enumerate(rows):
        x = 0
        for label, d, c in parts:
            ax.broken_barh([(x, d)], (y - 0.3, 0.6), facecolors=c)
            if d > 5: ax.text(x + d / 2, y, label, ha='center', va='center', fontsize=8.5)
            x += d
        ax.text(x + 1, y, f'≈ {x:.0f}µs／hop', va='center', fontsize=9)
    ax.set_yticks([0, 1]); ax.set_yticklabels([r[0] for r in rows], fontsize=9)
    ax.set_xlim(0, 90); ax.set_ylim(-0.6, 1.6)
    for s in ('top', 'right', 'left'): ax.spines[s].set_visible(False)
    ax.tick_params(axis='y', length=0); ax.set_xlabel('一個相依 hop 的時間（µs）')
    ax.set_title('(c) 計算放在哪裡，一個相依 hop 都要先等 tR：plane 內只省約 2µs；而且下一個節點幾乎都在別的 plane',
                 loc='left', fontsize=11, fontweight='bold')


def main():
    fig, axes = plt.subplots(3, 1, figsize=(14, 9.5), gridspec_kw=dict(height_ratios=[3, 3, 1.6]))
    nb = barrier(axes[0]); na = async_(axes[1])
    axes[1].set_xlabel('時間（µs）')
    per_hop(axes[2])
    items = [Patch(color=C['read'], label=f'flash 讀取 {READ:.0f}µs'), Patch(color=C['queue'], label='撞車排隊 10–60µs（約 30% 的讀取）'),
             Patch(color=C['cpu'], label=f'SSD 內展開計算 {CPU:.0f}µs（假設）')]
    fig.legend(handles=items, loc='upper center', ncol=3, frameon=False, fontsize=9.5, bbox_to_anchor=(0.5, 0.995))
    fig.text(0.01, 0.005, f'理想化示意：同一串讀取延遲，非同步比 barrier 多完成 {100 * (na / nb - 1):.0f}% 的讀取。'
             '非同步改變走訪順序，實際讀取數與 recall 需要用真實演算法量測（P3）。', fontsize=9, color='#444')
    fig.tight_layout(rect=(0, 0.015, 1, 0.965)); fig.savefig(sys.argv[1], dpi=140); print(sys.argv[1], nb, na)


if __name__ == '__main__':
    main()
