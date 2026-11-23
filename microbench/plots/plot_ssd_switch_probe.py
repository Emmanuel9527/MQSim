#!/usr/bin/env python3
"""T500 E10 (realssd/ssd_switch_probe.sh, 2026-11-23) with E8 (ssd_controller_probe.sh): 4 KiB random reads at QD 256
plus 8 KiB reads delivered evenly spaced, in bursts of B, or interleaved in one stream; 4 KiB reads displaced per
8 KiB read vs the 8 KiB rate.
Usage: plots/plot_ssd_switch_probe.py <E10 dir> <E8E9 dir> <out_dir>
"""
import csv, sys
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10, 'axes.spines.top': False,
                     'axes.spines.right': False})
E10, E8, OUT = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]); OUT.mkdir(parents=True, exist_ok=True)
rd = lambda d: {(r['run'], r['group']): (float(r['iops']), float(r['bw_MBps'])) for r in csv.DictReader(open(d / 'summary.csv'))}
A, B = rd(E10), rd(E8)
a10 = (A[('E10_alone', 'rand4k')][0] + A[('E10_alone_again', 'rand4k')][0]) / 2
a8 = (B[('E8_rand4k_qd256_alone', 'E8_rand4k_qd256_alone')][0] + B[('E8_rand4k_qd256_alone_again', 'E8_rand4k_qd256_alone_again')][0]) / 2
def two(S, run, alone):
    r4, r8 = S[(run, 'rand4k')][0], S[(run, 'mix8k')][0]
    return r8, (alone - r4) / r8
spread = sorted([two(A, f'E10_spread_r{r}', a10) for r in (5000, 10000)] +
                [two(B, f'E8_mix8k_r{r}', a8) for r in (10000, 20000, 40000)])
bursts = [(8, two(A, 'E10_burst8_t550', a10)), (32, two(A, 'E10_burst32_t2200', a10)), (32, two(A, 'E10_burst32_t5000', a10)),
          (128, two(A, 'E10_burst128_t10000', a10))]
mix = []
for p in (1, 2):
    tot = A[(f'E10_mix_{p}pct', 'mixstream')][0]; r8 = tot * p / 100; r4 = tot - r8
    mix.append((r8, (a10 - r4) / r8, p))
fig, ax = plt.subplots(figsize=(7.8, 4.8))
ax.plot([p[0] / 1e3 for p in spread], [p[1] for p in spread], 'o-', color='#8fa9c4', label='evenly spaced (E8 + E10): 1 per switch')
col = {8: '#d9a066', 32: '#e07a5f', 128: '#4a5a6a'}
for bsz, (x, y) in bursts:
    ax.scatter(x / 1e3, y, s=70, marker='s', color=col[bsz], zorder=3, label=f'bursts of {bsz}' if bsz != 32 or x < 5000 else None)
    ax.annotate(f'burst {bsz}', (x / 1e3, y), textcoords='offset points', xytext=(7, -3), fontsize=8, color=col[bsz])
for x, y, p in mix:
    ax.scatter(x / 1e3, y, s=70, marker='^', color='#9cbf9a', zorder=3, label='one stream, random mix (1 %, 2 %)' if p == 1 else None)
ax.axhline(6, color='#999999', ls='--', lw=1); ax.text(39, 6.6, 'additive per-command time: ~6', fontsize=8, color='#777777', ha='right')
ax.axhline(2, color='#999999', ls=':', lw=1); ax.text(39, 2.6, 'data-proportional: <= 2', fontsize=8, color='#777777', ha='right')
ax.set_xlabel('8 KiB reads per second (k)'); ax.set_ylabel('4 KiB reads displaced per 8 KiB read')
ax.set_xlim(0, 40); ax.set_ylim(0, 46); ax.grid(alpha=0.3); ax.legend(frameon=False, fontsize=8, loc='upper right')
ax.set_title('T500: 4 KiB at QD 256 plus 8 KiB reads, by delivery pattern', fontsize=10, loc='left')
fig.tight_layout(); fig.savefig(OUT / 'ssd_switch_probe_t500.png', dpi=130); plt.close(fig)
print('alone E10', a10, 'E8', a8)
for p in spread: print('spread', round(p[0]), round(p[1], 1))
for b, (x, y) in bursts: print('burst', b, round(x), round(y, 1))
for x, y, p in mix: print('mix', p, round(x), round(y, 1))
