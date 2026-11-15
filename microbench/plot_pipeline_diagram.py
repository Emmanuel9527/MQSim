#!/usr/bin/env python3
"""Pipeline diagrams of one DiskANN worker inside the calibrated T500 model.

  schematic <out.png>                                   idealised round (W=4, one plane collision), T16 W4 averages
  real <trace_dir> <run_dir> <out.png> [--worker 0 --rounds 42 45]
                                                        rounds taken from a closed-loop run with MQSIM_TRANSACTION_LOG

Real mode: the worker's reads are found from mqsim_rounds.txt + schedule.txt (arrival = round start + offset) and matched
to the first unused transaction whose issue_ns is at or just after the arrival (within 2µs).
Rows: host (gap / pre-issue / wait / post), every plane the worker reads in the window (its own reads in colour, other
workers' reads on that plane in light grey, queueing before dispatch as a red bar), and the 4 shared channels.
"""
import argparse
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

plt.rcParams['font.family'] = ['Noto Sans CJK TC', 'Noto Sans CJK JP', 'DejaVu Sans']
C = dict(cmd='#1a1a1a', tr='#c9ccd0', xfer='#14808a', other='#eceef0', queue='#d1495b', gap='#f2b134',
         pre='#7aa6c2', wait='#ffffff', post='#7a5c99')
H = 0.62


def bar(ax, y, s, e, color, edge=None, hatch=None, label=None, txtcolor='black', lw=0.6):
    if e <= s: return
    ax.broken_barh([(s, e - s)], (y - H / 2, H), facecolors=color, edgecolors=edge or 'none', hatch=hatch, linewidth=lw)
    if label: ax.text((s + e) / 2, y, label, ha='center', va='center', fontsize=8, color=txtcolor)


def legend(ax):
    items = [Patch(color=C['cmd'], label='指令 0.29µs'), Patch(color=C['tr'], label='array read（tR）70µs'),
             Patch(color=C['xfer'], label='資料傳出 1.7µs（4KB）'), Patch(color=C['queue'], label='排隊：等 plane 空出來'),
             Patch(facecolor=C['other'], edgecolor='#999', label='其他 worker 的讀取'),
             Patch(color=C['gap'], label='主機計算（輪間）'), Patch(color=C['pre'], label='送出前主機時間'),
             Patch(color=C['post'], label='完成後到 DiskANN')]
    ax.legend(handles=items, loc='lower center', bbox_to_anchor=(0.5, 1.0), ncol=4, frameon=False, fontsize=9)


def draw_read(ax, y, issue, cmd, es, ee, xs, xe, own=True):
    if own:
        bar(ax, y, issue, cmd, C['queue'])
        bar(ax, y, cmd, es, C['cmd']); bar(ax, y, es, ee, C['tr']); bar(ax, y, ee, xs, C['queue']); bar(ax, y, xs, xe, C['xfer'])
    else:
        bar(ax, y, cmd, xe, C['other'], edge='#aaa', lw=0.5)


def schematic(out):
    tR, cmd, xfer, gap, post = 70.02, 0.29, 1.707, 19.2, 2.7
    fig, ax = plt.subplots(figsize=(13, 5.2))
    rows = ['DiskANN worker（主機）', 'Plane P1', 'Plane P2', 'Plane P3', 'Plane P4', 'Channel（共用）']
    ys = {r: len(rows) - 1 - i for i, r in enumerate(rows)}
    t = 8.0
    chan = []
    for rnd, collide in enumerate([True, False]):
        rb = t + gap
        bar(ax, ys[rows[0]], t, rb, C['gap'], label=f'計算 {gap:.0f}')
        first = rb + 5.2
        bar(ax, ys[rows[0]], rb, first, C['pre'])
        ends = []
        for i in range(4):
            y = ys[f'Plane P{i + 1}']
            issue = first + 0.8 * i
            start = issue
            if collide and i == 2:   # another worker's read already occupies P3
                o0 = issue - 32
                draw_read(ax, y, 0, o0, o0 + cmd, o0 + cmd + tR, o0 + cmd + tR, o0 + cmd + tR + xfer, own=False)
                ax.text(o0 + 2, y + 0.42, '其他 worker 先到', fontsize=8, color='#666')
                start = o0 + cmd + tR + xfer
                chan.append((o0 + cmd + tR, start, '#8fb9bd'))
                ax.text(issue + (start - issue) / 2, y, f'排隊 {start - issue:.0f}µs', ha='center', va='center', fontsize=8,
                        color='white', zorder=5)
            es = start + cmd; ee = es + tR
            xs = max([ee] + [c[1] for c in chan if c[0] < ee + xfer and c[1] > ee]); xe = xs + xfer
            draw_read(ax, y, issue, start, es, ee, xs, xe)
            chan += [(start, es, C['cmd']), (xs, xe, C['xfer'])]
            ends.append(xe)
        done = max(ends) + 0.9
        bar(ax, ys[rows[0]], first, done, C['wait'], edge='#888', label='等這輪 W=4 筆全部回來')
        bar(ax, ys[rows[0]], done, done + post, C['post'])
        ax.axvline(done, color='#555', ls=':', lw=0.8)
        ax.text(done, ys[rows[0]] + 0.5, f'第 {rnd} 輪結束\n（最慢那筆決定）' if collide else f'第 {rnd} 輪結束', fontsize=8,
                ha='center', va='bottom')
        t = done + post
    for s, e, c in chan:
        bar(ax, ys[rows[-1]], s, e, c, lw=0)
    ax.set_yticks(list(ys.values())); ax.set_yticklabels(list(ys.keys()))
    ax.set_xlabel('時間（µs）'); ax.set_xlim(0, t + 4); ax.set_ylim(-0.7, len(rows) - 0.1 + 0.6)
    for sp in ('top', 'right', 'left'): ax.spines[sp].set_visible(False)
    ax.tick_params(axis='y', length=0)
    legend(ax)
    ax.text(0.0, -0.22, '示意：T16 W4 平均值（輪間計算 19.2µs、送出前 5.2µs、完成後 2.7µs、tR 70µs）。第 0 輪 P3 撞到其他 worker 的讀取，'
            '整輪被最慢那筆拖長；第 1 輪沒有撞車。channel 只有細細的傳輸，大部分時間閒置。', transform=ax.transAxes, fontsize=8.5, color='#444')
    fig.tight_layout(); fig.savefig(out, dpi=150); print(out)


def load_worker_reads(trace, run, worker, r0, r1):
    rounds = [l.split() for l in open(run / 'mqsim_rounds.txt').read().splitlines()[1:]]
    rounds = {int(x[2]): tuple(map(int, x[3:7])) for x in rounds if int(x[0]) == worker}
    sched = [l.split() for l in open(trace / 'closed_loop' / 'schedule.txt') if not l.startswith('#')]
    sched = [x for x in sched if int(x[0]) == worker]
    out = []
    for k in range(r0, r1 + 1):
        x = sched[k]; gap, post, n = int(x[2]), int(x[3]), int(x[4])
        offs = [int(x[5 + 3 * i]) for i in range(n)]
        start, last, end, _ = rounds[k]
        out.append(dict(round=k, start=start, last=last, end=end, gap=gap, post=post, arrivals=[start + o for o in offs]))
    return out


def real(trace, run, out, worker, r0, r1):
    rs = load_worker_reads(trace, run, worker, r0, r1)
    lo = rs[0]['start'] - rs[0]['gap'] - 5000; hi = rs[-1]['end'] + 5000
    x = np.loadtxt(run / 'mqsim_transactions.txt', skiprows=1, dtype=np.int64, usecols=range(9))
    win = x[(x[:, 5] >= lo) & (x[:, 0] <= hi)]
    order = np.argsort(win[:, 0]); win = win[order]
    own_idx, used = [], set()
    for r in rs:
        for a in r['arrivals']:
            j = int(np.searchsorted(win[:, 0], a))
            while j in used: j += 1
            if j >= len(win) or win[j, 0] - a > 2000: raise SystemExit(f'no transaction for arrival {a}')
            used.add(j); own_idx.append(j)
    unit = lambda t: (int(t[6]), int(t[7]), int(t[8]))
    units = sorted({unit(win[j]) for j in own_idx})
    rows = ['DiskANN worker（主機）'] + [f'ch{c} chip{p} die{d}' for c, p, d in units] + [f'Channel {c}' for c in range(4)]
    ys = {r: len(rows) - 1 - i for i, r in enumerate(rows)}
    us = lambda v: (v - lo) / 1e3
    fig, ax = plt.subplots(figsize=(14, 0.42 * len(rows) + 2.2))
    yh = ys[rows[0]]
    for r in rs:
        rb = r['start']; first = min(r['arrivals'])
        bar(ax, yh, us(rb - r['gap']), us(rb), C['gap'], label=f"{r['gap'] / 1e3:.0f}")
        bar(ax, yh, us(rb), us(first), C['pre'])
        bar(ax, yh, us(first), us(r['last']), C['wait'], edge='#888', label=f"第 {r['round']} 輪 {len(r['arrivals'])} 筆，{(r['end'] - rb) / 1e3:.0f}µs")
        bar(ax, yh, us(r['last']), us(r['end']), C['post'])
        ax.axvline(us(r['last']), color='#555', ls=':', lw=0.7)
    own_units = {unit(win[j]) for j in own_idx}
    for j, t in enumerate(win):
        u = unit(t)
        if u in own_units:
            name = f'ch{u[0]} chip{u[1]} die{u[2]}'
            draw_read(ax, ys[name], *map(us, t[:6]), own=(j in used))
        y = ys[f'Channel {t[6]}']
        bar(ax, y, us(t[1]), us(t[2]), C['cmd'], lw=0)
        bar(ax, y, us(t[4]), us(t[5]), C['xfer'] if j in used else '#8fb9bd', lw=0)
    ax.set_yticks(list(ys.values())); ax.set_yticklabels(list(ys.keys()), fontsize=8)
    ax.set_xlim(0, us(hi)); ax.set_ylim(-0.7, len(rows) + 0.2)
    ax.set_xlabel('時間（µs，視窗起點起算）')
    for sp in ('top', 'right', 'left'): ax.spines[sp].set_visible(False)
    ax.tick_params(axis='y', length=0)
    legend(ax)
    busy = {c: 0.0 for c in range(4)}
    for t in win:
        busy[int(t[6])] += (min(t[2], hi) - max(t[1], lo)).clip(0) + (min(t[5], hi) - max(t[4], lo)).clip(0)
    util = ' '.join(f'ch{c} {100 * b / (hi - lo):.0f}%' for c, b in busy.items())
    ax.text(0.0, -0.9 / (0.42 * len(rows) + 2.2), f'模擬實際截取：{trace.name}，worker {worker} 第 {r0}–{r1} 輪。'
            f'只畫這個 worker 讀到的 plane；淺灰為其他 worker 在同一 plane 的讀取。\n'
            f'視窗內 channel 使用率：{util}（深藍綠＝這個 worker 的傳輸，淺藍綠＝其他 worker）。',
            transform=ax.transAxes, fontsize=8.5, color='#444')
    fig.tight_layout(); fig.savefig(out, dpi=150); print(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest='mode', required=True)
    s = sp.add_parser('schematic'); s.add_argument('out', type=Path)
    r = sp.add_parser('real'); r.add_argument('trace', type=Path); r.add_argument('run', type=Path); r.add_argument('out', type=Path)
    r.add_argument('--worker', type=int, default=0); r.add_argument('--rounds', type=int, nargs=2, default=[42, 45])
    a = ap.parse_args()
    if a.mode == 'schematic': schematic(a.out)
    else: real(a.trace.resolve(), a.run.resolve(), a.out, a.worker, *a.rounds)


if __name__ == '__main__':
    main()
