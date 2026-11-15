#!/usr/bin/env python3
"""Beam width trade-off: dependent hops vs I/O per query, and the resulting latency floor vs plane-capacity QPS bound.

Measured (SIFT100M R64 L100, real T500 traces): W4/W8/W16. W32/64/96 are extrapolated (hops ~ W^-b fitted on the
measured points, I/O linear in W) and drawn hollow + dashed - they must be measured before any conclusion.
Latency floor = hops x 75 us (one dependent flash read per hop). QPS bound = 96 units / 75 us / (I/O per query).
Usage: plot_beamwidth_tradeoff.py <out.png>
"""
import sys
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams['font.family'] = ['Noto Sans CJK TC', 'Noto Sans CJK JP', 'DejaVu Sans']
INK, MUTED, SERIES = '#222222', '#6b6b6b', '#2a6f97'
Wm = np.array([4, 8, 16]); hops_m = np.array([30.9, 18.6, 12.7]); io_m = np.array([118.4, 134.7, 168.7])
b, loga = np.polyfit(np.log(Wm), np.log(hops_m), 1)
k, c = np.polyfit(Wm, io_m, 1)
We = np.array([32, 64, 96]); hops_e = np.exp(loga) * We ** b; io_e = c + k * We
READ, UNITS = 75e-6, 96
cap = UNITS / READ


def style(ax):
    for s in ('top', 'right'): ax.spines[s].set_visible(False)
    ax.grid(alpha=0.25, lw=0.6); ax.tick_params(colors=MUTED, labelsize=9)


def series(ax, xm, ym, xe, ye, labels_m, labels_e, fmt):
    ax.plot(xm, ym, '-o', color=SERIES, lw=2, ms=8)
    ax.plot(np.r_[xm[-1], xe], np.r_[ym[-1], ye], '--', color=SERIES, lw=1.5)
    ax.plot(xe, ye, 'o', mfc='white', mec=SERIES, mew=2, ms=8)
    for x, y, l in list(zip(xm, ym, labels_m)) + list(zip(xe, ye, labels_e)):
        off = (8, -14) if (l == 4 and ax.get_label() == 'io') else (6, 6)
        ax.annotate(fmt(l, y), (x, y), textcoords='offset points', xytext=off, fontsize=8.5, color=INK)


def main():
    fig, ax = plt.subplots(1, 3, figsize=(15, 4.6))
    series(ax[0], Wm, hops_m, We, hops_e, Wm, We, lambda w, y: f'W{w}: {y:.1f}')
    ax[0].set_xlabel('beam width W'); ax[0].set_ylabel('每 query 相依 hop 數'); ax[0].set_title('(a) W 越大，相依 hop 越少', loc='left')
    ax[1].set_label('io')
    series(ax[1], Wm, io_m, We, io_e, Wm, We, lambda w, y: f'W{w}: {y:.0f}')
    ax[1].set_xlabel('beam width W'); ax[1].set_ylabel('每 query 讀取數'); ax[1].set_title('(b) 但讀取數跟著增加', loc='left')
    qm, qe = cap / io_m / 1e3, cap / io_e / 1e3
    lm, le = hops_m * READ * 1e3, hops_e * READ * 1e3
    series(ax[2], qm, lm, qe, le, Wm, We, lambda w, y: f'W{w}')
    ax[2].set_xlabel('QPS 上限（千，96 單位全滿）'); ax[2].set_ylabel('延遲下限（ms，hop × 75µs）')
    ax[2].set_title('(c) 延遲下限 vs 吞吐上限', loc='left')
    ax[2].invert_xaxis()
    ax[2].text(0.02, 0.04, '往右下 → 延遲低，但吞吐上限也低', transform=ax[2].transAxes, fontsize=8.5, color=MUTED)
    for a in ax: style(a)
    fig.text(0.01, 0.01, f'實心＝實測（T500、SIFT100M R64 L100）；空心虛線＝外插（hop ∝ W^{b:.2f}、讀取數 ≈ {c:.0f}+{k:.1f}W），需實測。'
             'W96 時單一 query 就占滿全部 96 個平行單位。', fontsize=9, color=MUTED)
    fig.tight_layout(rect=(0, 0.04, 1, 1)); fig.savefig(sys.argv[1], dpi=140)
    print(sys.argv[1], 'b=%.3f' % b, 'io=%.1f+%.2fW' % (c, k), dict(zip(We.tolist(), np.round(hops_e, 1))), np.round(io_e), np.round(qe, 2), np.round(le, 2))


if __name__ == '__main__':
    main()
