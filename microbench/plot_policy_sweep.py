#!/usr/bin/env python3
"""P4: plot diskann_policy_sweep.sh summary.csv - hops, I/Os, recall vs beam width per frontier policy, and the
latency floor (hops x 75 us) vs plane-capacity QPS bound (96 units / 75 us / I/Os per query).
Usage: plot_policy_sweep.py <sweep_dir>   -> <sweep_dir>/policy_sweep.png, policy_sweep.json
"""
import csv, json, sys
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams['font.family'] = ['Noto Sans CJK TC', 'Noto Sans CJK JP', 'DejaVu Sans']
POL = ['base', 'unit', 'sector', 'unitsector']
LABEL = {'base': '原始', 'unit': '一單位一筆', 'sector': 'sector 內展開', 'unitsector': '一單位一筆＋sector 展開'}
COLOR = dict(zip(POL, ['#2a78d6', '#eb6834', '#1baf7a', '#eda100']))
MARK = dict(zip(POL, ['o', 's', '^', 'D']))
READ, UNITS = 75e-6, 96


def main():
    d = Path(sys.argv[1]).resolve()
    rows = [r for r in csv.DictReader(open(d / 'summary.csv')) if not r['run'].startswith('explog')]
    data = {p: sorted(([int(r['W']), float(r['mean_hops']), float(r['mean_ios']), float(r['recall'])]
                       for r in rows if r['run'].rsplit('_W', 1)[0] == p), key=lambda v: v[0]) for p in POL}
    out = {p: [dict(W=w, hops=h, ios=io, recall=rc, latency_floor_ms=h * READ * 1e3,
                    qps_bound=UNITS / READ / io) for w, h, io, rc in v] for p, v in data.items()}
    (d / 'policy_sweep.json').write_text(json.dumps(out, indent=2))
    fig, axs = plt.subplots(2, 2, figsize=(13, 9.5)); ax = axs.ravel()
    for p in POL:
        v = out[p]
        if not v: continue
        W = [x['W'] for x in v]
        kw = dict(color=COLOR[p], marker=MARK[p], ms=7, lw=2, label=LABEL[p])
        ax[0].plot(W, [x['hops'] for x in v], **kw)
        ax[1].plot(W, [x['ios'] for x in v], **kw)
        ax[2].plot(W, [x['recall'] for x in v], **kw)
        ax[3].plot([x['qps_bound'] / 1e3 for x in v], [x['latency_floor_ms'] for x in v], **kw)
        for x in v:
            if p == 'base': ax[3].annotate(f"W{x['W']}", (x['qps_bound'] / 1e3, x['latency_floor_ms']),
                                           textcoords='offset points', xytext=(5, 5), fontsize=8, color='#444')
    for a, t, yl in zip(ax, ['(a) 相依 hop', '(b) 每 query 讀取數', '(c) recall@10', '(d) 延遲下限 vs QPS 上限'],
                        ['hop', '讀取數', 'recall@10 (%)', '延遲下限 ms（hop × 75µs）']):
        a.set_title(t, loc='left'); a.set_ylabel(yl); a.grid(alpha=0.25)
        for s in ('top', 'right'): a.spines[s].set_visible(False)
    for a in ax[:3]: a.set_xscale('log', base=2); a.set_xlabel('beam width W')
    ax[3].set_xlabel('QPS 上限（千；96 單位全滿）'); ax[3].invert_xaxis()
    ax[0].legend(frameon=False, fontsize=9)
    fig.text(0.01, 0.01, f'{d.name}。hop／讀取數／recall 為實測；延遲下限與 QPS 上限為由 hop、讀取數推算的界線（不含排隊與主機時間）。',
             fontsize=9, color='#555')
    fig.tight_layout(rect=(0, 0.04, 1, 1)); fig.savefig(d / 'policy_sweep.png', dpi=130)
    for p in POL:
        print(p, [(x['W'], round(x['hops'], 1), round(x['ios'], 1), round(x['recall'], 2)) for x in out[p]])


if __name__ == '__main__':
    main()
