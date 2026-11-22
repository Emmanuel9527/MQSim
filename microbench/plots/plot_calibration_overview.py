#!/usr/bin/env python3
"""Calibration overview: how close the calibrated T500 MQSim model is to the real T500 (numbers from
mqsim_calibration.md: cmdfirst QD sweep, full closed-loop replay run_t500_full_20261115_050240, and the closed-loop
replays per (T, W) used as the N2 baseline).
(a) fio random 4 KiB read, IOPS vs queue depth; (b) DiskANN T16 W4 closed loop, per-round time quantiles;
(c) DiskANN closed loop QPS per (T, W).
Usage: plots/plot_calibration_overview.py <out.png>
"""
import sys
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams['font.family'] = ['DejaVu Sans']
INK, MUTED, GRID = '#333333', '#8a8a8a', '#e6e6e6'
REAL, SIM = '#b9b9b9', '#6f9bc8'

QD = [1, 4, 16, 32, 64, 128, 256, 512]
QD_REAL = [13.0, 49.7, 192, 357, 608, 862, 1028, 1048]
QD_SIM = [13.7, 54, 200, 365, 607, 873, 1054, 1059]
RQ = ['mean', 'p50', 'p90', 'p99', 'p99.9']
R_REAL = [131.39, 116.11, 173.82, 479.98, 716.04]
R_SIM = [131.50, 127.42, 192.51, 275.59, 361.88]
CFG = ['T8 W4', 'T16 W4', 'T32 W8', 'T48 W8', 'T32 W16', 'T48 W16']
Q_REAL = [2035, 3439, 4770, 4936, 3636, 4033]
Q_SIM = [2035, 3426, 5244, 5498, 3858, 3854]


def style(a):
    for s in ('top', 'right', 'left'):
        a.spines[s].set_visible(False)
    a.spines['bottom'].set_color('#bbbbbb')
    a.tick_params(axis='y', length=0, colors=MUTED, labelsize=9); a.tick_params(axis='x', colors=INK, labelsize=9)
    a.grid(axis='y', color=GRID, lw=0.8); a.set_axisbelow(True)


def bars(a, labels, real, sim, fmt):
    x = np.arange(len(labels)); bw = 0.38
    a.bar(x - bw / 2, real, bw * 0.92, color=REAL, label='real T500')
    a.bar(x + bw / 2, sim, bw * 0.92, color=SIM, label='MQSim (calibrated)')
    for xi, r, s in zip(x, real, sim):
        a.text(xi + bw / 2, s, fmt(100 * (s / r - 1)), ha='center', va='bottom', fontsize=8, color=INK)
    a.set_xticks(x); a.set_xticklabels(labels)


def main():
    fig, ax = plt.subplots(1, 3, figsize=(17, 4.8), gridspec_kw=dict(width_ratios=[1.1, 1, 1.2]))
    pct = lambda v: f'{v:+.0f}%' if abs(v) >= 1 else f'{v:+.1f}%'

    a = ax[0]; style(a)
    a.plot(QD, QD_REAL, color='#9a9a9a', marker='o', ms=6, lw=2.2, label='real T500')
    a.plot(QD, QD_SIM, color=SIM, marker='s', ms=5, lw=2, ls=(0, (4, 2)), label='MQSim (calibrated)')
    a.set_xscale('log', base=2); a.set_xticks(QD); a.set_xticklabels([str(q) for q in QD])
    a.set_xlabel('queue depth', color=INK); a.set_ylabel('kIOPS', color=INK)
    for q, r, s in zip(QD, QD_REAL, QD_SIM):
        if q in (1, 32, 128, 512):
            a.annotate(pct(100 * (s / r - 1)), (q, s), xytext=(0, 8), textcoords='offset points', ha='center',
                       fontsize=8, color=INK)
    a.set_title('(a) fio random 4K read: IOPS vs QD\n(used for fitting: 0–8% apart)', loc='left', fontsize=11,
                color=INK)
    a.legend(frameon=False, fontsize=9, loc='upper left')

    a = ax[1]; style(a)
    bars(a, RQ, R_REAL, R_SIM, pct)
    a.set_ylabel('per-round read time (µs)', color=INK)
    a.axvspan(2.5, 4.5, color='#fbeee9', zorder=0)
    a.text(3.5, max(R_REAL) * 1.02, 'tail: model too light', ha='center', fontsize=8.5, color='#c0654a')
    a.set_title('(b) DiskANN T16 W4 closed loop: round time\n(mean +0.1%, tail about half)', loc='left',
                fontsize=11, color=INK)
    a.set_ylim(0, max(R_REAL) * 1.12)

    a = ax[2]; style(a)
    bars(a, CFG, Q_REAL, Q_SIM, pct)
    a.axvspan(-0.5, 1.5, color='#f2f6fa', zorder=0)
    a.text(0.5, max(Q_SIM) * 1.06, 'low load: matches', ha='center', fontsize=8.5, color=SIM)
    a.text(3.5, max(Q_SIM) * 1.06, 'high load: model 6–11% fast', ha='center', fontsize=8.5, color=MUTED)
    a.set_ylabel('queries / s', color=INK); a.set_ylim(0, max(Q_SIM) * 1.13)
    a.set_title('(c) DiskANN closed loop: QPS per (T, W)', loc='left', fontsize=11, color=INK)
    a.legend(frameon=False, fontsize=9, loc='upper left', bbox_to_anchor=(0, 0.93))

    fig.suptitle('Calibrated T500 MQSim model vs the real Crucial T500 (labels: model vs real)', x=0.01, ha='left',
                 fontsize=12.5, color=INK)
    fig.text(0.01, 0.01, 'Config: ssdconfig_t500_calibrated.xml (4 ch, 96 plane units, tR 70 µs fitted, central cap ~1.06M '
             'IOPS). Closed loop: host times from the real trace, SSD times simulated. Sources: mqsim_calibration.md.',
             fontsize=8, color=MUTED)
    fig.tight_layout(rect=(0, 0.04, 1, 0.94)); fig.savefig(sys.argv[1], dpi=150); print(sys.argv[1])


if __name__ == '__main__':
    main()
