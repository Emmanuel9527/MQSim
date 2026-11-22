#!/usr/bin/env python3
"""N2 diagram: how one simulated round joins measured host time and modelled SSD time on one virtual clock.
Widths are to scale, T16 W4 means (host profile from the real trace; SSD part from the original-policy simulation).
Usage: plots/plot_sim_time_alignment.py <out.png>
"""
import sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

plt.rcParams['font.family'] = ['DejaVu Sans']
INK, MUTED = '#333333', '#8a8a8a'
HOST, HOST_D = '#f1dcb8', '#c39550'   # measured on the real machine
SSD, SSD_D = '#cfe0f1', '#4f7fae'     # modelled
# (label, µs, kind); T16 W4 means
SEGS = [('host compute', 17.2, 'h'), ('submit', 5.2, 'h'),
        ('queue', 16.5, 's'), ('cmd', 0.29, 's'), ('array read tR', 70.02, 's'), ('channel + interface', 3.65, 's'),
        ('post', 2.7, 'h')]
H = 6.0


def main():
    fig, ax = plt.subplots(figsize=(12, 3.4))
    x = 0; pos = []
    for lab, w, k in SEGS:
        pos.append((x, w)); x += w
    end = x
    # bars: host segments as separate rounded boxes, SSD part as one box with white dividers
    for (sx, w), (lab, _, k) in zip(pos, SEGS):
        if k == 'h':
            ax.add_patch(FancyBboxPatch((sx + 0.15, 0), w - 0.3, H, boxstyle='round,pad=0,rounding_size=0.8',
                                        fc=HOST, ec=HOST_D, lw=1.1))
    s0 = pos[2][0]; s1 = pos[-1][0]
    ax.add_patch(FancyBboxPatch((s0 + 0.15, 0), s1 - s0 - 0.3, H, boxstyle='round,pad=0,rounding_size=0.8',
                                fc=SSD, ec=SSD_D, lw=1.1))
    for (sx, w), (lab, _, k) in zip(pos[3:6], SEGS[3:6]):
        ax.plot([sx, sx], [0.7, H - 0.7], color='white', lw=1.6)
    # next round stub
    ax.add_patch(FancyBboxPatch((end + 0.15, 0), 9, H, boxstyle='round,pad=0,rounding_size=0.8', fc=HOST, ec=HOST_D,
                                lw=1.1, alpha=0.45))
    ax.text(end + 11, H / 2, 'next round …', va='center', fontsize=9.5, color=MUTED)
    # labels: inside if wide enough, else below; durations always below
    for (sx, w), (lab, us, k) in zip(pos, SEGS):
        c = sx + w / 2
        dur = f'{us:.0f} µs' if us >= 1 else f'{us:.2f} µs'
        if w >= 14:
            ax.text(c, H / 2 + 0.7, lab, ha='center', va='center', fontsize=10, color=INK)
            ax.text(c, H / 2 - 1.0, dur, ha='center', va='center', fontsize=9, color=MUTED)
    below = [(pos[1], SEGS[1], 0), (pos[3], SEGS[3], 0), (pos[5], SEGS[5], 0), (pos[6], SEGS[6], 1)]
    for (sx, w), (lab, us, k), row in below:
        c = sx + w / 2
        dur = f'{us:.0f} µs' if us >= 1 else f'{us:.2f} µs'
        yy = -1.0 - 2.1 * row
        ax.plot([c, c], [0, yy + 0.3], color='#bbbbbb', lw=0.8)
        ha = 'right' if lab.startswith('channel') else 'center'
        ax.text(c + (0.8 if ha == 'right' else 0), yy, f'{lab}  {dur}', ha=ha, va='top', fontsize=8.5, color=INK)

    # brackets
    def bracket(x0, x1, y, text, color, up=True, fs=10):
        d = 1.3 if up else -1.3
        ax.plot([x0 + 0.3, x0 + 0.3, x1 - 0.3, x1 - 0.3], [y, y + d, y + d, y], color=color, lw=1.3)
        ax.text((x0 + x1) / 2, y + d + (0.6 if up else -0.6), text, ha='center', va='bottom' if up else 'top',
                fontsize=fs, color=color, fontweight='bold')
    bracket(0, s0, H + 0.6, 'measured (real T500 trace)', HOST_D)
    bracket(s1, end, H + 0.6, 'measured', HOST_D, fs=9)
    bracket(s0, s1, H + 0.6, 'simulated (calibrated MQSim SSD parameters)', SSD_D)

    ax.set_xlim(-1, end + 24); ax.set_ylim(-6.2, H + 4.6); ax.axis('off')
    ax.set_title('One round in the N2 simulator: measured host time + simulated SSD time on one virtual clock',
                 loc='left', fontsize=12, color=INK)
    fig.text(0.01, 0.03, 'Widths to scale, T16 W4 means. Host times are sampled per round from the measured '
             'distribution of each (T, W); the real I/O time of the trace is replaced by the SSD model.',
             fontsize=8, color=MUTED)
    fig.tight_layout(rect=(0, 0.06, 1, 1)); fig.savefig(sys.argv[1], dpi=150); print(sys.argv[1])


if __name__ == '__main__':
    main()
