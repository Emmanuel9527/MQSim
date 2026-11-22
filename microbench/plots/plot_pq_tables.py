#!/usr/bin/env python3
"""Schematic of DiskANN's PQ codebook (pivots) and the per-query distance table (SIFT100M server: D=128, M=31).
Usage: plots/plot_pq_tables.py <out_dir>
Writes <out_dir>/pq_codebook.png and <out_dir>/pq_query_table.png (palette and style of plots/plot_ssd_idle_load.py).
Cells are elided with ellipses; only a few rows and chunks are drawn.
"""
import sys
from pathlib import Path
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

plt.rcParams['font.family'] = ['DejaVu Sans']
C = dict(host='#e9c9a0', io='#a9bfd4', read='#8fa9c4', other='#e4e4e4', queue='#e07a5f', pf='#9cc9b0',
         ink='#333333', muted='#888888', axis='#bbbbbb')
out = Path(sys.argv[1])
ROWS = ['k = 0', 'k = 1', 'k = 2', None, 'k = 255']   # center index within each chunk; None = ellipsis row
HL_ROW = 1                                                          # highlighted center


def cell(ax, x, y, color, text=None, edge='white', lw=1.5, z=1):
    ax.add_patch(Rectangle((x, y), 1, 1, facecolor=color, edgecolor=edge, linewidth=lw, zorder=z))
    if text:
        ax.text(x + 0.5, y + 0.5, text, ha='center', va='center', fontsize=8, color=C['ink'], zorder=z + 1)


def bracket(ax, x0, x1, y, label):
    ax.plot([x0 + 0.08, x0 + 0.08, x1 - 0.08, x1 - 0.08], [y, y + 0.18, y + 0.18, y], color=C['muted'], lw=1)
    ax.text((x0 + x1) / 2, y + 0.38, label, ha='center', va='bottom', fontsize=8.5, color=C['ink'])


def rows_axis(ax, ncols_x):
    n = len(ROWS)
    for i, r in enumerate(ROWS):
        y = n - 1 - i
        ax.text(-0.25, y + 0.5, r if r else '⋮', ha='right', va='center',
                fontsize=10 if r else 13, color=C['ink'])
    return n


def finish(fig, ax, title, note, path):
    ax.set_title(title, loc='left', fontsize=11.5, color=C['ink'], pad=14)
    ax.set_aspect('equal'); ax.axis('off')
    fig.text(0.01, 0.015, note, fontsize=8, color=C['muted'])
    fig.tight_layout(rect=(0, 0.04, 1, 1)); fig.savefig(path, dpi=180); plt.close(fig); print('->', path)


def codebook():
    # columns: chunk 0 (dims 0-4), chunk 1 (dims 5-9), ellipsis, chunk 30 (dims 124-127)
    groups = [('chunk 0: its own 256 centers\nk-means on dims 0–4', list(range(0, 5)), C['read']),
              ('chunk 1: its own 256 centers\nk-means on dims 5–9', list(range(5, 10)), C['io']),
              (None, None, None),
              ('chunk 30: its own 256 centers\nk-means on dims 124–127', list(range(124, 128)), C['host'])]
    fig, ax = plt.subplots(figsize=(10, 4.6))
    n = rows_axis(ax, None)
    x, gap = 0, 1.0
    for name, dims, col in groups:
        if name is None:
            for i, r in enumerate(ROWS):
                ax.text(x + 0.6, n - 1 - i + 0.5, '⋯' if r else '', ha='center', va='center', fontsize=13, color=C['muted'])
            ax.text(x + 0.6, n + 0.5, '⋯', ha='center', va='center', fontsize=11, color=C['muted'])
            x += 1.2 + gap; continue
        for j, d in enumerate(dims):
            ax.text(x + j + 0.5, n + 0.5, f'd{d}', ha='center', va='center', fontsize=7.5, color=C['muted'])
            for i, r in enumerate(ROWS):
                y = n - 1 - i
                if r is None:
                    ax.text(x + j + 0.5, y + 0.5, '⋮', ha='center', va='center', fontsize=11, color=C['muted'])
                else:
                    cell(ax, x + j, y, col, 'f32')
        ax.add_patch(Rectangle((x - 0.12, -0.12), len(dims) + 0.24, n + 0.24, fill=False, edgecolor=C['muted'],
                               linewidth=1, linestyle=(0, (3, 2)), zorder=0))
        bracket(ax, x, x + len(dims), n + 1.05, name)
        if name.startswith('chunk 0'):
            yh = n - 1 - HL_ROW
            ax.add_patch(Rectangle((x, yh), len(dims), 1, fill=False, edgecolor=C['queue'], linewidth=2.2, zorder=5))
            hl_x = x
        x += len(dims) + gap
    ax.text(hl_x, -0.65, 'boxed: center k = 1 of chunk 0 = 5 float32 (20 B), a point in dims 0–4 only',
            va='center', fontsize=9, color=C['queue'])
    tx = x - gap + 0.8
    ax.text(tx, 3.9, 'row k = 31 unrelated centers', fontsize=9, color=C['ink'], va='center')
    ax.text(tx, 3.4, 'side by side (same index only),', fontsize=9, color=C['ink'], va='center')
    ax.text(tx, 2.9, 'not one 128-dim point', fontsize=9, color=C['ink'], va='center')
    ax.text(tx, 1.5, 'each cell = 1 float32 (4 B)', fontsize=9, color=C['ink'], va='center')
    ax.text(tx, 0.6, '256 × 128 × 4 B = 128 KB', fontsize=10, color=C['ink'], va='center', fontweight='bold')
    ax.set_xlim(-1.6, tx + 6.6); ax.set_ylim(-1.1, n + 2.6)
    finish(fig, ax, 'Codebook (pivots): 31 independent codebooks of 256 centers, stored side by side = 128 KB',
           'SIFT100M server PQ, 31 chunks (4 of 5 dims, 27 of 4). Each chunk: k-means++ then 12 Lloyd rounds on '
           '~256k sampled base points, using only its own dims', out / 'pq_codebook.png')


def query_table():
    chunks = [0, 1, 2, None, 30]
    cols = {0: C['read'], 1: C['io'], 2: C['pf'], 30: C['host']}
    fig, ax = plt.subplots(figsize=(10, 4.6))
    n = rows_axis(ax, None)
    x, w = 0, 1.6
    for m in chunks:
        if m is None:
            for i, r in enumerate(ROWS):
                ax.text(x + 0.5, n - 1 - i + 0.5, '⋯' if r else '', ha='center', va='center', fontsize=13, color=C['muted'])
            ax.text(x + 0.5, n + 0.5, '⋯', ha='center', va='center', fontsize=11, color=C['muted'])
            x += 1.2; continue
        ax.text(x + w / 2, n + 0.5, f'chunk {m}', ha='center', va='center', fontsize=8.5, color=C['ink'])
        for i, r in enumerate(ROWS):
            y = n - 1 - i
            if r is None:
                ax.text(x + w / 2, y + 0.5, '⋮', ha='center', va='center', fontsize=11, color=C['muted'])
                continue
            k = r.split()[-1]
            ax.add_patch(Rectangle((x, y), w, 1, facecolor=cols[m], edgecolor='white', linewidth=1.5))
            ax.text(x + w / 2, y + 0.5, f'd[{k},{m}]', ha='center', va='center', fontsize=8, color=C['ink'])
        if m == 0:
            ax.add_patch(Rectangle((x, n - 1 - HL_ROW), w, 1, fill=False, edgecolor=C['queue'], linewidth=2.2, zorder=5))
            hl_x = x
        x += w
    tx = x + 0.6
    ax.text(hl_x, -0.55, "boxed: d[1,0] = 1 float32 (4 B) = squared distance between q's dims 0–4 and center 1 of chunk 0",
            va='center', fontsize=9, color=C['queue'])
    ax.text(tx, 1.5, 'PQ distance of a point = sum of 31 lookups,', fontsize=9, color=C['ink'], va='center')
    ax.text(tx, 1.0, 'one per chunk, picked by its 31-byte code', fontsize=9, color=C['ink'], va='center')
    ax.text(tx, 0.3, '256 × 31 × 4 B = 31 KB', fontsize=10, color=C['ink'], va='center', fontweight='bold')
    ax.set_xlim(-2.2, tx + 8.5); ax.set_ylim(-1.0, n + 1.2)
    finish(fig, ax, 'Query distance table: 256 centers × 31 chunks, float32 = 31 KB',
           'Rebuilt for every query from the query vector and the codebook; '
           'the 31-byte codes of all 100M points (3.1 GB) are a separate array', out / 'pq_query_table.png')


codebook()
query_table()
