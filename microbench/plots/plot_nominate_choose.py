#!/usr/bin/env python3
"""Idealised timeline of "host nominates, SSD chooses" vs the original DiskANN round when one plane is busy.

Two separate figures on the same time axis:
  <prefix>_a.png  original: host sends its top-W; #3 sits on a plane busy with another query's read, so the whole
                  round waits for it (barrier).
  <prefix>_b.png  host nominates top-2W; the SSD skips the busy plane and reads #5 instead, then reads #3 at low
                  priority once its plane frees (prefetch for a later round).
Host times are the T16 W4 averages.
Usage: plots/plot_nominate_choose.py <out_prefix>
"""
import sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

plt.rcParams['font.family'] = ['DejaVu Sans']
GAP, PRE, POST, READ = 19.2, 5.2, 2.7, 75.0
BUSY_LEFT = 40.0
C = dict(host='#e9c9a0', io='#a9bfd4', read='#8fa9c4', other='#e4e4e4', queue='#e07a5f', pf='#9cc9b0',
         ssd='#4a5a78', ink='#333333', muted='#888888')


def draw(out, nominate):
    rows = ['Host'] + (['SSD'] if nominate else []) + ['Plane A', 'Plane B', 'Plane C', 'Plane D'] + \
           (['Plane E'] if nominate else [])
    y = {r: len(rows) - 1 - i for i, r in enumerate(rows)}
    fig, ax = plt.subplots(figsize=(9, 0.55 * len(rows) + 1.3))

    def bar(r, s, e, c, t=None, edge='none', tc=None):
        ax.broken_barh([(s, e - s)], (y[r] - 0.3, 0.6), facecolors=c, edgecolors=edge, linewidth=0.8)
        if t:
            ax.text((s + e) / 2, y[r], t, ha='center', va='center', fontsize=9, color=tc or C['ink'])

    rb = GAP; issue = rb + PRE
    bar('Host', 0, rb, C['host'], 'compute'); bar('Host', rb, issue, C['io'])
    bar('Plane C', issue - (READ - BUSY_LEFT), issue + BUSY_LEFT, C['other'], 'other query', tc=C['muted'])
    if not nominate:
        for r, lab in [('Plane A', '#1'), ('Plane B', '#2'), ('Plane D', '#4')]:
            bar(r, issue, issue + READ, C['read'], lab)
        bar('Plane C', issue + BUSY_LEFT, issue + BUSY_LEFT + READ, C['read'], '#3')
        ax.broken_barh([(issue, BUSY_LEFT)], (y['Plane C'] - 0.42, 0.08), facecolors=C['queue'])
        ax.text(issue + BUSY_LEFT / 2, y['Plane C'] - 0.62, f'#3 waits {BUSY_LEFT:.0f} µs', ha='center',
                va='center', fontsize=8.5, color=C['queue'])
        done = issue + BUSY_LEFT + READ
        title = 'Original: host sends top-W, round waits for the busy plane'
    else:
        bar('SSD', issue, issue + 1.5, C['ssd'])
        ax.text(issue + 4, y['SSD'], 'C busy: read #5 now, #3 later', fontsize=8.5, va='center', color=C['ink'])
        for r, lab in [('Plane A', '#1'), ('Plane B', '#2'), ('Plane D', '#4'), ('Plane E', '#5')]:
            bar(r, issue + 1.5, issue + 1.5 + READ, C['read'], lab)
        bar('Plane C', issue + BUSY_LEFT, issue + BUSY_LEFT + READ, C['pf'], '#3 prefetch')
        done = issue + 1.5 + READ
        title = 'Host nominates top-2W, SSD chooses free planes'
    bar('Host', issue, done, 'white', 'wait for W=4', edge='#bbbbbb', tc=C['muted'])
    bar('Host', done, done + POST, C['io'])
    end = done + POST
    ax.axvline(end, color=C['muted'], ls=(0, (2, 3)), lw=0.8)
    ax.text(end + 2, y['Host'], f'round {done - rb:.0f} µs', fontsize=10, va='center', fontweight='bold',
            color=C['ink'])

    ax.set_title(title, loc='left', fontsize=11.5, color=C['ink'], pad=26)
    ax.set_yticks(list(y.values())); ax.set_yticklabels(rows, fontsize=9.5, color=C['ink'])
    ax.set_xlim(0, 200); ax.set_ylim(-0.9, len(rows) - 0.4)
    ax.set_xlabel('time (µs)', fontsize=9.5, color=C['ink'])
    for s in ('top', 'right', 'left'):
        ax.spines[s].set_visible(False)
    ax.spines['bottom'].set_color('#bbbbbb')
    ax.tick_params(axis='y', length=0); ax.tick_params(axis='x', colors=C['muted'], labelsize=8.5)

    items = [Patch(color=C['host'], label='host compute'), Patch(color=C['io'], label='submit / complete'),
             Patch(color=C['read'], label=f'flash read ({READ:.0f} µs)'), Patch(color=C['other'], label='other query')]
    items += [Patch(color=C['ssd'], label='SSD choice'), Patch(color=C['pf'], label='low-priority prefetch')] \
        if nominate else [Patch(color=C['queue'], label='queueing')]
    ax.legend(handles=items, loc='lower left', bbox_to_anchor=(0, 1.0), ncol=len(items), frameon=False,
              fontsize=8, handlelength=1.2, columnspacing=1.2, borderaxespad=0.2)
    h = fig.get_figheight()
    fig.subplots_adjust(left=0.085, right=0.97, bottom=0.75 / h, top=1 - 0.95 / h)
    fig.savefig(out, dpi=150); plt.close(fig); print(out)


def main():
    p = sys.argv[1]
    draw(f'{p}_a.png', False)
    draw(f'{p}_b.png', True)


if __name__ == '__main__':
    main()
