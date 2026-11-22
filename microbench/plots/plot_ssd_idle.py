#!/usr/bin/env python3
"""Where the 'SSD is mostly idle under original DiskANN' numbers come from (diskann_inssd_study.md, 2026-11-15 Part A).
Input: slack.json from trace_replay/analyze_ssd_slack.py (MQSim transaction log, calibrated T500 model, num_nodes_to_cache=0,
head/tail 5% trimmed). Round composition: the per-round table in the same study section (closed-loop replay).
Usage: plots/plot_ssd_idle.py <T8_slack.json> <T16_slack.json> <out.png>
Panels: (a) plane (parallel unit) time breakdown, (b) channel time breakdown (mean of 4 channels),
        (c) one round on the critical path: why most units idle yet each round still waits.
"""
import json, sys
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams['font.family'] = ['Noto Sans CJK JP', 'DejaVu Sans']
BLUE, ORANGE, AQUA, YELLOW, GRAY, INK, MUTED = '#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#d9d8d3', '#33332f', '#6b6a65'
runs = [('T8 W4', json.load(open(sys.argv[1]))), ('T16 W4', json.load(open(sys.argv[2])))]
out = sys.argv[3]

# Per-round critical path (µs), study md table "每一輪的時間組成": device floor ~73 µs, queue = slowest read - 73.
rounds = {'T8 W4': [('送出前主機', 4.5, MUTED), ('裝置下限 (tR+指令+傳輸)', 73.0, BLUE), ('最慢那筆的排隊', 101.3 - 73.0, ORANGE),
                    ('完成→DiskANN', 2.5 + 0.8, YELLOW), ('輪間主機計算', 17.9, AQUA)],
          'T16 W4': [('送出前主機', 5.2, MUTED), ('裝置下限 (tR+指令+傳輸)', 73.0, BLUE), ('最慢那筆的排隊', 122.8 - 73.0, ORANGE),
                     ('完成→DiskANN', 2.7 + 0.8, YELLOW), ('輪間主機計算', 19.2, AQUA)]}

fig, axs = plt.subplots(3, 1, figsize=(11, 8.2), gridspec_kw={'height_ratios': [1, 1, 1.1]})
y = [1, 0]
print('config plane_read held idle_queued idle_empty | ch_data ch_cmd ch_idle | concurrent_reads mean/p90 | queue_us')


def hbar(ax, parts, title, xmax=100, unit='%'):
    for yi, (name, segs) in zip(y, parts):
        left = 0
        for lab, v, col in segs:
            ax.barh(yi, v, left=left, color=col, height=0.55, edgecolor='white', linewidth=2, label=lab if yi == 1 else None)
            if v >= (6 if unit == '%' else 8):
                ax.text(left + v / 2, yi, f'{v:.1f}' if unit != '%' else f'{v:.1f}%', ha='center', va='center',
                        fontsize=9.5, color='white' if col not in (GRAY, YELLOW) else INK)
            left += v
        if unit != '%':
            ax.text(left + 1.5, yi, f'{left:.0f} µs', va='center', fontsize=10, color=INK)
    ax.set_yticks(y, [p[0] for p in parts]); ax.set_xlim(0, xmax); ax.set_title(title, loc='left', fontsize=11.5, color=INK)
    for s in ('top', 'right', 'left'): ax.spines[s].set_visible(False)
    ax.tick_params(axis='y', length=0)
    ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.2), ncol=5, frameon=False, fontsize=9.5)


plane, chan = [], []
for name, d in runs:
    u = d['unit_time_breakdown']; ch = d['channel_utilization'].values()
    cd = 100 * sum(c['data'] for c in ch) / 4; cc = 100 * sum(c['cmd'] for c in d['channel_utilization'].values()) / 4
    plane.append((name, [('array read (tR)', 100 * u['array_read'], BLUE), ('held（指令/等 channel/傳出）', 100 * u['held'], ORANGE),
                         ('有工作卻閒著', 100 * u['idle_queued'], YELLOW), ('完全空閒', 100 * u['idle_empty'], GRAY)]))
    chan.append((name, [('資料傳送', cd, BLUE), ('指令傳送', cc, ORANGE), ('閒置', 100 - cd - cc, GRAY)]))
    ca = d['concurrent_array_reads']
    print(name, *(f'{100 * u[k]:.2f}' for k in ('array_read', 'held', 'idle_queued', 'idle_empty')), '|',
          f'{cd:.1f} {cc:.1f} {100 - cd - cc:.1f}', '|', f"{ca['mean']:.1f}/{ca['p90']}", '|',
          f"{d['per_read_waits_us']['queued_before_dispatch_mean']:.1f}")

hbar(axs[0], plane, '(a) 96 個平行單位（plane）的時間分解：69–82% 完全沒有工作；同時在讀的平均只有 17／28 個')
hbar(axs[1], chan, '(b) 4 條 channel 的時間分解（平均）：約 80–88% 閒置')
hbar(axs[2], [(n, rounds[n]) for n in ('T8 W4', 'T16 W4')],
     '(c) 每一輪的關鍵路徑：每輪等 W 筆全部回來，最慢那筆常撞到正在讀的 plane', xmax=165, unit='µs')
fig.suptitle('原始 DiskANN 下 SSD 內部大多空閒（MQSim 校準 T500 模型，低負載，無 node cache）', fontsize=13, color=INK, x=0.01, ha='left')
fig.tight_layout(); fig.savefig(out, dpi=160); print('->', out)
