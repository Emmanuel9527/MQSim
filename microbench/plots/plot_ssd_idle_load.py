#!/usr/bin/env python3
"""SSD-internal time breakdown across load (MQSim closed-loop replay of the node-cache-100k traces, calibrated T500).
Usage: plots/plot_ssd_idle_load.py <out_prefix> <label>=<slack.json> [<label>=<slack.json> ...]
Writes three figures (palette and style of plots/plot_nominate_choose.py):
  <out_prefix>_plane.png    96 parallel units: array read / held / idle with work queued / idle with nothing to do
  <out_prefix>_channel.png  4 channels (mean): data transfer / command / idle
  <out_prefix>_queue.png    mean per-read latency split into queueing (wait for the plane) + tR + the rest
                            (command, data out, bus wait, host interface, completion); total from comparison.json
Prints the numbers it plots.
"""
import json, sys
from pathlib import Path
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams['font.family'] = ['DejaVu Sans', 'Noto Sans CJK JP']
C = dict(host='#e9c9a0', io='#a9bfd4', read='#8fa9c4', other='#e4e4e4', queue='#e07a5f',
         ink='#333333', muted='#888888', axis='#bbbbbb')
prefix = sys.argv[1]
runs = [(a.split('=', 1)[0], json.load(open(a.split('=', 1)[1]))) for a in sys.argv[2:]]
lat = [json.load(open(Path(a.split('=', 1)[1]).parent / 'comparison.json'))['request_latency_us']['sim']['mean'] for a in sys.argv[2:]]
n = len(runs); ys = list(range(n - 1, -1, -1))
SUB = 'MQSim calibrated T500 model, node cache 100k, closed-loop replay of real traces'


def style(ax, xlabel):
    for s in ('top', 'right', 'left'): ax.spines[s].set_visible(False)
    ax.spines['bottom'].set_color(C['axis'])
    ax.tick_params(axis='x', colors=C['muted'], labelsize=9, length=0)
    ax.tick_params(axis='y', length=0, labelsize=10, colors=C['ink'])
    ax.set_xlabel(xlabel, fontsize=9.5, color=C['ink'])


def finish(fig, ax, title, path, ncol):
    ax.set_title(title, loc='left', fontsize=11.5, color=C['ink'], pad=30)
    ax.legend(loc='lower left', bbox_to_anchor=(0, 1.0), ncol=ncol, frameon=False, fontsize=8.5,
              handlelength=1.2, columnspacing=1.4, borderaxespad=0.2)
    fig.text(0.01, 0.01, SUB, fontsize=8, color=C['muted'])
    fig.tight_layout(rect=(0, 0.03, 1, 1)); fig.savefig(path, dpi=180); plt.close(fig); print('->', path)


def stacked(path, title, rows, small_note):
    """rows: [(label, [(seg_name, pct, color), ...])]; segments < 6% are listed after the bar instead of inside."""
    fig, ax = plt.subplots(figsize=(10, 0.62 * n + 1.9))
    for yi, (name, segs) in zip(ys, rows):
        left, notes = 0, []
        for lab, v, col in segs:
            ax.barh(yi, v, left=left, color=col, height=0.58, edgecolor='white', linewidth=1.5,
                    label=lab if yi == ys[0] else None)
            if v >= 6:
                ax.text(left + v / 2, yi, f'{v:.1f}%', ha='center', va='center', fontsize=9, color=C['ink'])
            elif lab in small_note:
                notes.append(f'{small_note[lab]} {v:.2g}%' if v < 1 else f'{small_note[lab]} {v:.1f}%')
            left += v
        if notes:
            ax.text(101.5, yi, '  '.join(notes), va='center', fontsize=8.5, color=C['muted'])
    ax.set_xlim(0, 125); ax.set_xticks(range(0, 101, 20))
    ax.set_yticks(ys, [r[0] for r in rows]); style(ax, 'share of time (%)')
    finish(fig, ax, title, path, len(rows[0][1]))


plane, chan, wait = [], [], []
print('config array_read held idle_queued idle_empty | ch_data ch_cmd ch_idle | conc_mean queue_us')
for name, d in runs:
    u = d['unit_time_breakdown']; ch = list(d['channel_utilization'].values())
    cd = 100 * sum(c['data'] for c in ch) / len(ch); cc = 100 * sum(c['cmd'] for c in ch) / len(ch)
    plane.append((name, [('array read (tR)', 100 * u['array_read'], C['read']),
                         ('held (cmd / wait channel / data out)', 100 * u['held'], C['io']),
                         ('idle, work queued', 100 * u['idle_queued'], C['queue']),
                         ('idle, nothing to do', 100 * u['idle_empty'], C['other'])]))
    chan.append((name, [('data transfer', cd, C['read']), ('command', cc, C['io']), ('idle', 100 - cd - cc, C['other'])]))
    q = d['per_read_waits_us']['queued_before_dispatch_mean']; c = d['concurrent_array_reads']['mean']
    wait.append((name, q, c))
    print(name, *(f'{100 * u[k]:.2f}' for k in ('array_read', 'held', 'idle_queued', 'idle_empty')), '|',
          f'{cd:.1f} {cc:.1f} {100 - cd - cc:.1f}', '|', f'{c:.1f} {q:.1f}')

stacked(f'{prefix}_plane.png', '96 parallel units (planes): idle time falls from 82% to 31% as load rises', plane,
        {'held (cmd / wait channel / data out)': 'held', 'idle, work queued': 'queued'})
stacked(f'{prefix}_channel.png', '4 channels (mean): never the bottleneck', chan, {'command': 'cmd'})

fig, ax = plt.subplots(figsize=(10, 0.62 * n + 1.9))
TR = 70.02
print('config queue tR rest total')
for yi, (name, q, c), tot in zip(ys, wait, lat):
    rest = tot - q - TR
    print(name, f'{q:.1f} {TR:.1f} {rest:.1f} {tot:.1f}')
    left = 0
    for lab, v, col in (('queueing (wait for the plane)', q, C['queue']), ('flash array read (tR)', TR, C['read']),
                        ('cmd + data out + interface', rest, C['io'])):
        ax.barh(yi, v, left=left, color=col, height=0.58, edgecolor='white', linewidth=1.5,
                label=lab if yi == ys[0] else None)
        if v >= 6:
            ax.text(left + v / 2, yi, f'{v:.1f}', ha='center', va='center', fontsize=9, color=C['ink'])
        left += v
    ax.text(tot + 2, yi, f'{tot:.1f} µs', va='center', fontsize=9.5, color=C['ink'], fontweight='bold')
    ax.text(225, yi, f'{c:.0f} / 96 planes reading', va='center', ha='right', fontsize=9, color=C['muted'])
ax.set_xlim(0, 228); ax.set_xticks(range(0, 181, 30)); ax.set_yticks(ys, [w[0] for w in wait])
style(ax, 'mean latency per read (µs)')
finish(fig, ax, 'Per-read latency: queueing grows to more than one tR at T48 W8', f'{prefix}_queue.png', 3)
