#!/usr/bin/env python3
"""Idealised pipelines of the candidate architectures (no plane collisions), on one time axis.

Panels: baseline DiskANN, host hints (H), SSD prefetch one round ahead (S, d=1), SSD prefetch two rounds ahead (d=2),
whole traversal inside the SSD. Host times are the T16 W4 averages; device read = tR + command + transfer + interface.
Assumed (not measured): SSD-buffer hit latency HIT, in-SSD compute per round SSD_CPU.
Usage: plot_architecture_pipelines.py <out.png>
"""
import sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

plt.rcParams['font.family'] = ['Noto Sans CJK TC', 'Noto Sans CJK JP', 'DejaVu Sans']
GAP, PRE, POST, READ, HIT, SSD_CPU, W = 19.2, 5.2, 2.7, 75.0, 15.0, 3.0, 4
C = dict(gap='#f2b134', pre='#7aa6c2', post='#7a5c99', wait='#ffffff', read='#c9ccd0', hit='#14808a', pf='#9fd3c7',
         pfwait='#d1495b', ssd='#3d5a80', miss='#c9ccd0')
T_END = 470


class Panel:
    def __init__(self, ax, title, rows):
        self.ax, self.rows = ax, rows
        self.y = {r: len(rows) - 1 - i for i, r in enumerate(rows)}
        ax.set_title(title, loc='left', fontsize=11, fontweight='bold')
        ax.set_yticks(list(self.y.values())); ax.set_yticklabels(rows, fontsize=9)
        ax.set_xlim(0, T_END); ax.set_ylim(-0.6, len(rows) - 0.4)
        for s in ('top', 'right', 'left'): ax.spines[s].set_visible(False)
        ax.tick_params(axis='y', length=0)

    def bar(self, row, s, e, color, label=None, lane=None, lanes=1, edge=None, tc='black'):
        if e <= s: return
        h = 0.7 / lanes; y0 = self.y[row] - 0.35 + (lane or 0) * h
        self.ax.broken_barh([(s, e - s)], (y0 + 0.12 * h * (lanes > 1), h * (1 - 0.24 * (lanes > 1))), facecolors=color,
                            edgecolors=edge or 'none', linewidth=0.6)
        if label: self.ax.text((s + e) / 2, y0 + h / 2, label, ha='center', va='center', fontsize=7.5, color=tc)

    def host_round(self, start, read_lat, label=''):
        """Host side of one round starting at `start` (beginning of the compute gap). Returns (issue, end)."""
        rb = start + GAP; issue = rb + PRE; done = issue + read_lat
        self.bar('主機', start, rb, C['gap'], '計算')
        self.bar('主機', rb, issue, C['pre'])
        self.bar('主機', issue, done, C['wait'], label, edge='#888')
        self.bar('主機', done, done + POST, C['post'])
        self.ax.axvline(done + POST, color='#bbb', ls=':', lw=0.7)
        return issue, done + POST

    def note(self, text):
        self.ax.text(T_END - 2, len(self.rows) - 0.55, text, ha='right', va='bottom', fontsize=8.5, color='#333')


def reads(p, row, issue, lat, colors, lanes=W, base=0):
    for i in range(W):
        c = colors[i] if isinstance(colors, list) else colors
        L = lat[i] if isinstance(lat, list) else lat
        p.bar(row, issue + 0.8 * i, issue + 0.8 * i + L, c, lane=base + i, lanes=lanes)


def baseline(ax):
    p = Panel(ax, '(0) 原始 DiskANN：每輪等 W 筆讀完（barrier）', ['主機', 'NAND 讀取'])
    t = 0
    while t < T_END:
        issue, t2 = p.host_round(t, READ + 2.4, f'等 W={W} 筆')
        reads(p, 'NAND 讀取', issue, READ, C['read'])
        t = t2
    p.note(f'每輪 ≈ {GAP + PRE + READ + 2.4 + POST:.0f}µs（無撞車；實測含排隊約 151）')


def host_hint(ax):
    p = Panel(ax, '(1) 主機提示 H：送第 r 輪時，附上候選佇列第 W+1… 名，SSD 用閒置 plane 預讀', ['主機', 'NAND 讀取', 'NAND 預讀'])
    t = 0
    # round 0: normal, hints for round 1 issued along
    issue, t1 = p.host_round(t, READ + 2.4, '第 r 輪')
    reads(p, 'NAND 讀取', issue, READ, C['read']); reads(p, 'NAND 預讀', issue + 1, READ, C['pf'])
    # round 1: 3 of 4 hit, one miss -> barrier keeps the round long
    issue, t2 = p.host_round(t1, READ + 2.4, '3 筆命中、1 筆沒中')
    reads(p, 'NAND 讀取', issue, [HIT, HIT, HIT, READ], [C['hit'], C['hit'], C['hit'], C['miss']])
    reads(p, 'NAND 預讀', issue + 1, READ, C['pf'])
    # round 2: all hit
    issue, t3 = p.host_round(t2, HIT + 2.4, '')
    reads(p, 'NAND 讀取', issue, HIT, C['hit'])
    pf2 = issue + 1
    reads(p, 'NAND 預讀', pf2, READ, C['pf'])
    req3 = t3 + GAP + PRE
    remain = pf2 + READ - req3
    issue, t4 = p.host_round(t3, remain + 2.4, f'只等剩下 {remain:.0f}')
    reads(p, 'NAND 讀取', issue, remain, C['pfwait'])
    ax.text(issue - 50, p.y['NAND 讀取'] + 0.48, '全部命中才變短', ha='center', fontsize=8, color=C['hit'])
    ax.text(issue + 30, p.y['NAND 讀取'] + 0.48, '上一輪太快 → 提示的提前量不夠', ha='left', fontsize=8, color=C['pfwait'])
    p.note('第 r 輪送出提示，提前量約 150µs，夠藏 tR；但只要 1 筆沒中，整輪照樣等 tR')


def ssd_d1(ax):
    p = Panel(ax, '(2) SSD 預讀 d=1：讀完第 r 輪就算出第 r+1 輪，馬上開始讀', ['主機', 'SSD 計算', 'NAND 讀取'])
    issue, t = p.host_round(0, READ + 2.4, '第 r 輪')
    reads(p, 'NAND 讀取', issue, READ, C['read'])
    rd_start = issue
    while t < T_END:
        ready = rd_start + READ + 2.4                      # SSD sees the last neighbour list of this round
        p.bar('SSD 計算', ready, ready + SSD_CPU, C['ssd'])
        pf0 = ready + SSD_CPU
        reads(p, 'NAND 讀取', pf0, READ, C['pf'])
        req = t + GAP + PRE                                 # host asks for the next round here
        remain = pf0 + READ - req
        ax.plot([req], [p.y['NAND 讀取'] + 0.42], marker='v', color=C['pfwait'], ms=6)
        _, t = p.host_round(t, remain + 2.4, f'只等剩下 {remain:.0f}')
        rd_start = pf0
    ax.text(5, p.y['NAND 讀取'] + 0.5, '▼ 主機送出下一輪請求的時間（預讀已在進行）', fontsize=7.5, color=C['pfwait'])
    p.note(f'提前量只有主機往返約 {POST + GAP + PRE:.0f}µs → 每輪約省 {POST + GAP + PRE - SSD_CPU:.0f}µs，藏不住 tR')


def ssd_d2(ax):
    p = Panel(ax, '(3) SSD 預讀 d=2：讀完第 r 輪就預讀第 r+2 輪（往前兩輪）', ['主機', 'SSD 計算', 'NAND 讀取', 'NAND 預讀'])
    t = 0
    lat = [READ + 2.4, READ + 2.4, HIT + 2.4, HIT + 2.4, HIT + 2.4, HIT + 2.4, HIT + 2.4]
    ends = []
    for k, L in enumerate(lat):
        if t >= T_END: break
        issue, t2 = p.host_round(t, L, f'第 r+{k} 輪' if k < 2 else '')
        reads(p, 'NAND 讀取', issue, L - 2.4, C['read'] if k < 2 else C['hit'])
        ready = issue + L
        p.bar('SSD 計算', ready, ready + SSD_CPU, C['ssd'])
        reads(p, 'NAND 預讀', ready + SSD_CPU, READ, C['pf'], lanes=2 * W, base=W * (k % 2))
        t = t2
    p.note(f'穩態每輪 ≈ {GAP + PRE + HIT + 2.4 + POST:.0f}µs（假設全部命中）；兩輪約 88µs 剛好蓋過一次 flash 讀取，預讀同時有 2W 筆在途')


def in_ssd(ax):
    p = Panel(ax, '(4) 整個走訪搬進 SSD：主機只送 query、收結果', ['主機', 'SSD 計算', 'NAND 讀取'])
    p.bar('主機', 0, 5, C['pre'], ''); p.bar('主機', 5, T_END, '#f4f4f4', '主機空出來（可做其他 query），每個 query 只有 1 個命令', edge='#ccc')
    t = 5
    while t < T_END:
        reads(p, 'NAND 讀取', t, READ, C['read'])
        e = t + READ + 2.4
        p.bar('SSD 計算', e, e + SSD_CPU, C['ssd'])
        t = e + SSD_CPU
        p.ax.axvline(t, color='#bbb', ls=':', lw=0.7)
    p.note(f'每輪 ≈ {READ + 2.4 + SSD_CPU:.0f}µs；PQ codes 要在 SSD 內（記憶體成本）；也可再加 (3) 的投機')


def main():
    out = sys.argv[1]
    fig, axes = plt.subplots(5, 1, figsize=(14, 15.5), sharex=True,
                             gridspec_kw=dict(height_ratios=[2, 3, 3, 4, 3]))
    for f, ax in zip([baseline, host_hint, ssd_d1, ssd_d2, in_ssd], axes): f(ax)
    axes[-1].set_xlabel('時間（µs）')
    items = [Patch(color=C['gap'], label=f'主機計算 {GAP}µs'), Patch(color=C['pre'], label=f'送出前 {PRE}µs'),
             Patch(color=C['post'], label=f'完成後 {POST}µs'), Patch(facecolor='white', edgecolor='#888', label='主機等待'),
             Patch(color=C['read'], label=f'flash 讀取 ≈{READ:.0f}µs'), Patch(color=C['pf'], label='預讀（低優先權）'),
             Patch(color=C['hit'], label=f'SSD 緩衝命中 {HIT:.0f}µs（假設）'), Patch(color=C['ssd'], label=f'SSD 內計算 {SSD_CPU:.0f}µs（假設）'), Patch(color=C['pfwait'], label='等進行中的預讀完成')]
    fig.legend(handles=items, loc='upper center', ncol=5, frameon=False, fontsize=9.5, bbox_to_anchor=(0.5, 0.995))
    fig.text(0.01, 0.003, '理想化示意（無 plane 撞車）。主機時間取 T16 W4 平均；緩衝命中延遲與 SSD 內計算時間為假設值，待 P2 用 MQSim 量化。',
             fontsize=9, color='#444')
    fig.tight_layout(rect=(0, 0.01, 1, 0.965)); fig.savefig(out, dpi=140); print(out)


if __name__ == '__main__':
    main()
