#!/usr/bin/env python3
"""Idealised timeline of "host nominates, SSD chooses" vs the original DiskANN round when one plane is busy.

(a) original: host sends its top-W; candidate #3 sits on a plane busy with another query's read -> the whole round
    waits for it (barrier). (b) host nominates top-2W; the SSD sees the busy plane and reads #5 instead, and reads
    #3 at low priority once its plane frees (prefetch for a later round). Host times are the T16 W4 averages.
Usage: plot_nominate_choose.py <out.png>
"""
import sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

plt.rcParams['font.family'] = ['Noto Sans CJK TC', 'Noto Sans CJK JP', 'DejaVu Sans']
GAP, PRE, POST, READ = 19.2, 5.2, 2.7, 75.0
BUSY_LEFT = 40.0
C = dict(gap='#f2b134', pre='#7aa6c2', post='#7a5c99', read='#c9ccd0', other='#eceef0', queue='#d1495b',
         pf='#9fd3c7', ssd='#3d5a80')
ROWS = ['主機', 'SSD 挑選', 'plane A', 'plane B', 'plane C（忙）', 'plane D', 'plane E']


def panel(ax, title, nominate):
    y = {r: len(ROWS) - 1 - i for i, r in enumerate(ROWS)}
    bar = lambda r, s, e, c, t=None, edge=None: (ax.broken_barh([(s, e - s)], (y[r] - 0.32, 0.64), facecolors=c,
                                                               edgecolors=edge or 'none', linewidth=0.6),
                                                ax.text((s + e) / 2, y[r], t, ha='center', va='center', fontsize=8) if t else None)
    rb = GAP; issue = rb + PRE
    bar('主機', 0, rb, C['gap'], '計算'); bar('主機', rb, issue, C['pre'])
    bar('plane C（忙）', issue - (READ - BUSY_LEFT), issue + BUSY_LEFT, C['other'], '其他 query 的讀取', edge='#aaa')
    if not nominate:
        ax.text(issue - 4, y['主機'] + 0.45, '送出 #1–#4', fontsize=8, ha='left')
        for r, lab in [('plane A', '#1'), ('plane B', '#2'), ('plane D', '#4')]:
            bar(r, issue, issue + READ, C['read'], lab)
        bar('plane C（忙）', issue + BUSY_LEFT, issue + BUSY_LEFT + READ, C['read'], '#3')
        ax.broken_barh([(issue, BUSY_LEFT)], (y['plane C（忙）'] - 0.42, 0.1), facecolors=C['queue'])
        done = issue + BUSY_LEFT + READ
        ax.text(issue + BUSY_LEFT / 2, y['plane C（忙）'] - 0.62, f'#3 排隊 {BUSY_LEFT:.0f}µs', ha='center', fontsize=8,
                color=C['queue'])
    else:
        ax.text(issue - 4, y['主機'] + 0.45, '提名 #1–#8（只送 ID 與排名）', fontsize=8, ha='left')
        bar('SSD 挑選', issue, issue + 1.5, C['ssd'])
        ax.text(issue + 3, y['SSD 挑選'], 'plane C 忙 → 改讀 #5；#3 等 C 空出再低優先權預讀', fontsize=8, va='center')
        for r, lab in [('plane A', '#1'), ('plane B', '#2'), ('plane D', '#4'), ('plane E', '#5')]:
            bar(r, issue + 1.5, issue + 1.5 + READ, C['read'], lab)
        bar('plane C（忙）', issue + BUSY_LEFT, issue + BUSY_LEFT + READ, C['pf'], '#3 預讀（之後的輪可能命中）')
        done = issue + 1.5 + READ
    bar('主機', issue, done, 'white', '等這輪 W=4 筆', edge='#888')
    bar('主機', done, done + POST, C['post'])
    ax.axvline(done + POST, color='#555', ls=':', lw=0.8)
    ax.text(done + POST + 2, y['主機'], f'輪時間 {done - rb:.0f}µs', fontsize=9, va='center', fontweight='bold')
    ax.set_title(title, loc='left', fontsize=11, fontweight='bold')
    ax.set_yticks(list(y.values())); ax.set_yticklabels(ROWS, fontsize=9)
    ax.set_xlim(0, 210); ax.set_ylim(-0.9, len(ROWS) - 0.2)
    for s in ('top', 'right', 'left'): ax.spines[s].set_visible(False)
    ax.tick_params(axis='y', length=0)


def main():
    fig, ax = plt.subplots(2, 1, figsize=(13, 8.2), sharex=True)
    panel(ax[0], '(a) 原始：主機直接送前 W 名，#3 撞上忙碌的 plane，整輪等它', False)
    panel(ax[1], '(b) 主機提名、SSD 挑選：提名前 2W 名，SSD 避開忙碌 plane 改讀 #5，#3 留作預讀', True)
    ax[1].set_xlabel('時間（µs）')
    items = [Patch(color=C['read'], label=f'flash 讀取 {READ:.0f}µs'), Patch(color=C['queue'], label='排隊'),
             Patch(facecolor=C['other'], edgecolor='#aaa', label='其他 query 的讀取'), Patch(color=C['pf'], label='低優先權預讀'),
             Patch(color=C['ssd'], label='SSD 依 plane 狀態挑選')]
    fig.legend(handles=items, loc='upper center', ncol=5, frameon=False, fontsize=9.5, bbox_to_anchor=(0.5, 0.995))
    fig.text(0.01, 0.005, '理想化示意。代價：#5 排名比 #3 後面，可能讓搜尋多走幾輪或掉 recall，這正是實驗要量的。',
             fontsize=9, color='#444')
    fig.tight_layout(rect=(0, 0.02, 1, 0.96)); fig.savefig(sys.argv[1], dpi=140); print(sys.argv[1])


if __name__ == '__main__':
    main()
