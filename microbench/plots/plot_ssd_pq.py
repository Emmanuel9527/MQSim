#!/usr/bin/env python3
"""Figures for the 2026-11-22 SSD-side PQ study (all in-figure text English, muted palette).
Usage: plots/plot_ssd_pq.py <results/autonomous_20261122> <figure dir>
Writes ssdpq_precision.png, ssdpq_footprint.png, ssdpq_coloc.png, ssdpq_autonomy.png (each only if its inputs exist).
"""
import json, sys
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker

plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10, 'axes.spines.top': False,
                     'axes.spines.right': False})
C = dict(host='#e9c9a0', io='#a9bfd4', read='#8fa9c4', other='#e4e4e4', queue='#e07a5f', dark='#4a5a6a',
         green='#9cbf9a')
R, OUT = Path(sys.argv[1]), Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)


def precision():
    fs = [R / f'coarse_pred_W{w}.json' for w in (4, 8)]
    if not all(f.exists() for f in fs): return
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), sharey=True)
    for ax, f in zip(axes, fs):
        j = json.load(open(f)); W = j['W']; S = j['specs']
        fam = {'PQ, 8-bit codes': [s for s in S if s.startswith('PQ') and s.endswith('x8')],
               'PQ, 4-bit codes': [s for s in S if s.startswith('PQ') and s.endswith('x4')]}
        for metric, col, lab in (('hybrid_k2W_coverage', C['read'], f'host hint + SSD, prefetch 2W'),
                                 ('hybrid_k1W_coverage', C['queue'], f'host hint + SSD, exactly W'),
                                 ('local_early5', C['dark'], 'SSD alone, rounds 0-4')):
            for fname, specs in fam.items():
                specs = sorted(specs, key=lambda s: S[s]['bytes'])
                ax.plot([S[s]['bytes'] for s in specs], [100 * S[s][metric] for s in specs],
                        marker='o' if '8-bit' in fname else 's', ls='-' if '8-bit' in fname else '--', color=col,
                        lw=1.8, ms=5, label=f'{lab} ({fname})')
            if 'RBQ' in S:
                ax.plot(S['RBQ']['bytes'], 100 * S['RBQ'][metric], marker='*', ms=12, color=col, ls='none')
            ax.axhline(100 * S['exact'][metric], color=col, lw=0.8, ls=':')
        ax.set_xscale('log', base=2); ax.set_xticks([2, 4, 8, 16, 20, 32]); ax.set_xticklabels(['2', '4', '8', '16', '20', '32'])
        ax.set_xlabel('SSD-side code size (bytes per vector)')
        ax.set_title(f'W={W}: next-round reads predicted (% of host frontier)', loc='left', fontsize=10)
        ax.set_ylim(0, 102); ax.grid(alpha=0.25)
    axes[0].set_ylabel('% of next-round reads covered')
    h, l = axes[0].get_legend_handles_labels()
    fig.legend(h, l, loc='lower center', ncol=3, frameon=False, fontsize=8)
    fig.text(0.99, 0.005, 'dotted: same predictor with exact distances; star: 1-bit RaBitQ-style (20 B)',
             ha='right', fontsize=7.5, color='#666')
    fig.tight_layout(rect=(0, 0.13, 1, 1))
    fig.savefig(OUT / 'ssdpq_precision.png', dpi=140); plt.close(fig)


def footprint():
    fs = [R / f'footprint_W{w}.json' for w in (4, 8)]
    if not all(f.exists() for f in fs): return
    fig, axes = plt.subplots(1, 3, figsize=(14, 4), gridspec_kw={'width_ratios': [1.3, 1.3, 1]})
    for ax, f in zip(axes[:2], fs):
        j = json.load(open(f)); W = j['W']; p = np.array(j['profile'])
        rr = [r for r in range(1, 30) if p[r, 5] >= 500]
        n = p[rr, 5]
        cached = (p[rr, 0] - p[rr, 3]) / n
        newv = p[rr, 4] / n
        oldv = (p[rr, 3] - p[rr, 4]) / n
        ax.bar(rr, cached, color=C['host'], label='cached on host (no SSD read)')
        ax.bar(rr, newv, bottom=cached, color=C['queue'], label='SSD read, new neighbour (needs SSD-side PQ)')
        ax.bar(rr, oldv, bottom=cached + newv, color=C['io'], label='SSD read, old candidate (host hint)')
        ax.set_xlabel('round r'); ax.set_ylabel('reads per round')
        ax.set_title(f'W={W}, 100k node cache: who can predict round r', loc='left', fontsize=10)
    h, l = axes[0].get_legend_handles_labels()
    fig.legend(h, l, loc='lower center', ncol=3, frameon=False, fontsize=8.5, bbox_to_anchor=(0.36, 0))
    for ax in axes[:2]:
        ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
    ax = axes[2]
    for f, col in zip(fs, (C['dark'], C['queue'])):
        j = json.load(open(f)); W = j['W']
        for k, ls in (('nocache', '--'), ('cache', '-')):
            h = j['hot'][k]
            ax.plot(h['N'], [100 * c for c in h['coverage']], ls=ls, marker='o', ms=4, color=col,
                    label=f"W={W}, {'no cache' if k == 'nocache' else '100k cache'}")
    ax.set_xscale('log'); ax.set_ylim(0, 60); ax.grid(alpha=0.25)
    ax.set_xlabel('hottest nodes kept resident\n(ranked on other queries)')
    ax.set_ylabel('% of early-phase SSD reads covered')
    ax.set_title('Early-phase reads are not concentrated', loc='left', fontsize=10)
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout(rect=(0, 0.07, 1, 1)); fig.savefig(OUT / 'ssdpq_footprint.png', dpi=140); plt.close(fig)


def coloc():
    d = R / 'coloc'
    if not d.exists(): return
    rows = [json.load(open(f)) for f in sorted(d.glob('*.json'))]
    if not rows: return
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.3))
    panels = [
        (axes[0], 1000, 'sensed', {('default', 'none', False): (C['dark'], '-', 'o', 'default layout, DiskANN'),
                                   ('default', 'sector', False): (C['dark'], ':', '^', 'default layout, expand sector'),
                                   ('shuffle', 'none', False): (C['read'], '-', 'o', 'graph-aware layout, DiskANN'),
                                   ('shuffle', 'sector', False): (C['queue'], '-', '^', 'graph-aware, expand 4 KiB sector'),
                                   ('shuffle', 'sector', True): (C['queue'], '--', 'v', 'graph-aware, sector, only if beats L-th')},
         '4 KiB sector expansion (1000 queries)'),
        (axes[1], 300, 'sensed', {('default', 'none', False): (C['dark'], '-', 'o', 'default layout, DiskANN'),
                                  ('default', 'page', False): (C['dark'], ':', 's', 'default layout, expand page'),
                                  ('shuffle', 'none', False): (C['read'], '-', 'o', 'graph-aware layout, DiskANN'),
                                  ('shuffle', 'page', False): (C['queue'], '-', 's', 'graph-aware, expand 16 KiB page'),
                                  ('shuffle', 'page', True): (C['queue'], '--', 'D', 'graph-aware, page, only if beats L-th')},
         '16 KiB page expansion (300 queries)')]
    for ax, nq, xk, style, title in panels:
        for key, (col, ls, mk, lab) in style.items():
            rs = sorted([r for r in rows if (r['layout'], r['coexp'], r['coexp_filter']) == key and r['queries'] == nq],
                        key=lambda r: r['L'])
            if not rs: continue
            ax.plot([r['mean'][xk] for r in rs], [100 * r['mean']['recall'] for r in rs], color=col, ls=ls, marker=mk,
                    ms=5, lw=1.8, label=lab)
        ax.set_xlabel('flash pages sensed per query (tR count)'); ax.set_ylabel('recall@10 (%)')
        ax.set_title(title, loc='left', fontsize=10); ax.grid(alpha=0.25); ax.legend(frameon=False, fontsize=7.5, loc='lower right')
    ax = axes[2]
    for key, (col, mk, lab) in {('default', 'none', False): (C['dark'], 'o', 'DiskANN'),
                                ('shuffle', 'sector', False): (C['queue'], '^', 'graph-aware + sector'),
                                ('shuffle', 'sector', True): (C['read'], 'v', 'graph-aware + sector, filtered')}.items():
        rs = sorted([r for r in rows if (r['layout'], r['coexp'], r['coexp_filter']) == key and r['queries'] == 1000],
                    key=lambda r: r['L'])
        ax.plot([r['mean']['sensed'] for r in rs], [r['mean']['expanded'] for r in rs], color=col, marker=mk, ms=5, lw=1.8, label=lab)
    ax.set_yscale('log'); ax.set_xlabel('flash pages sensed per query (tR count)')
    ax.set_ylabel('nodes expanded per query (distance work)')
    ax.set_title('The price: nodes whose neighbours are scored', loc='left', fontsize=10)
    ax.grid(alpha=0.25); ax.legend(frameon=False, fontsize=7.5)
    fig.text(0.99, 0.005, 'local SIFT100M R64 graph (not the server build), W=4, L = 50/70/100/150, host PQ32x8',
             ha='right', fontsize=7.5, color='#666')
    fig.tight_layout(rect=(0, 0.03, 1, 1)); fig.savefig(OUT / 'ssdpq_coloc.png', dpi=140); plt.close(fig)


def autonomy():
    d = R / 'autonomy'
    if not d.exists(): return
    rows = [json.load(open(f)) for f in sorted(d.glob('*.json'))]
    if not rows: return
    get = {(r['ssd_spec'], r['ssd_rounds']): r for r in rows if r['ssd_rounds'] > 0 and r['host'] == 'PQ32x8'}
    specs = ['exact', 'PQ16x8', 'RBQ', 'PQ8x8', 'PQ16x4', 'PQ4x8', 'PQ8x4']
    nbytes = {'exact': 128, 'PQ16x8': 16, 'RBQ': 20, 'PQ8x8': 8, 'PQ16x4': 8, 'PQ4x8': 4, 'PQ8x4': 4}
    ks = [3, 5, 8, 12]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.3))
    ax = axes[0]; wbar = 0.11
    cols = plt.cm.cividis(np.linspace(0.1, 0.9, len(specs)))
    for si, (sp, col) in enumerate(zip(specs, cols)):
        ys = []
        for k in ks:
            r, c = get.get((sp, k)), get.get(('PQ32x8', k))
            ys.append(100 * (r['mean']['reads'] / c['mean']['reads'] - 1) if r and c else np.nan)
        ax.bar(np.arange(len(ks)) + (si - len(specs) / 2 + 0.5) * wbar, ys, wbar, color=col, label=f'{sp} ({nbytes[sp]} B)')
    ax.axhline(0, color='#888', lw=0.8)
    ax.set_xticks(range(len(ks))); ax.set_xticklabels([f'k={k}' for k in ks])
    ax.set_xlabel('rounds run inside the SSD with the coarse code')
    ax.set_ylabel('extra reads vs SSD with host-grade PQ32x8 (%)')
    ax.set_title('Cost of a coarse SSD code: extra reads', loc='left', fontsize=10)
    ax.legend(frameon=False, fontsize=7.5, ncol=2); ax.grid(alpha=0.25, axis='y')
    ax = axes[1]
    base = sorted([json.load(open(f)) for f in (R / 'coloc').glob('default_none_L*.json')
                   if json.load(open(f))['queries'] == 1000], key=lambda r: r['L'])
    ax.plot([r['mean']['reads'] for r in base], [100 * r['mean']['recall'] for r in base], color='#888', lw=1.5,
            marker='o', ms=4, label='host only, L = 50/70/100/150')
    for sp, col in zip(['PQ32x8'] + specs, ['#e9c9a0'] + list(cols)):
        pts = [get[(sp, k)] for k in ks if (sp, k) in get]
        ax.plot([r['mean']['reads'] for r in pts], [100 * r['mean']['recall'] for r in pts], ls='none', marker='o',
                ms=5, color=col, label=None if sp != 'PQ32x8' else 'SSD-first, PQ32x8 (control)')
    for sp in ('PQ4x8', 'PQ8x4'):
        r = get.get((sp, 12))
    hs = [r for r in rows if r['ssd_rounds'] == 0 and r['host'] != 'PQ32x8']
    ax.set_xlim(60, 180); ax.set_ylim(89, 98.5)
    ax.set_xlabel('reads per query'); ax.set_ylabel('recall@10 (%)')
    ax.set_title('SSD-first points sit on the host-only L curve', loc='left', fontsize=10)
    ax.legend(frameon=False, fontsize=7.5, loc='lower right'); ax.grid(alpha=0.25)
    txt = 'whole search with coarse code (no host PQ): ' + ', '.join(
        f"{r['host']} {100 * r['mean']['recall']:.0f}%" for r in sorted(hs, key=lambda r: -nbytes.get(r['host'], 0)))
    ax.text(0.02, 0.97, txt, transform=ax.transAxes, fontsize=7.5, va='top', color='#a04030')
    fig.text(0.99, 0.005, 'local SIFT100M R64 graph, W=4, L=100, host PQ32x8, 1000 queries; colours as left panel',
             ha='right', fontsize=7.5, color='#666')
    fig.tight_layout(rect=(0, 0.03, 1, 1)); fig.savefig(OUT / 'ssdpq_autonomy.png', dpi=140); plt.close(fig)


def timing():
    f = R / 'coloc3_timing.jsonl'
    if not f.exists(): return
    rows = [json.loads(l) for l in open(f)]
    style = [('default_none', 'default layout, DiskANN', '#888888', '-', 'o'),
             ('p40_none', 'page-packed layout, DiskANN', C['read'], '-', 'o'),
             ('shuffle_page_fPQ8x8k16', 'sector-packed + page exp., 8 B filter (strict)', C['green'], ':', 'v'),
             ('p40_page_fPQ32x8_', 'page-packed + page exp., host-grade filter', C['queue'], '-', 's'),
             ('p40_page_fPQ8x8_', 'page-packed + page exp., 8 B filter', C['queue'], '--', 'D'),
             ('p40_page_fPQ4x8_', 'page-packed + page exp., 4 B filter', C['queue'], ':', '^'),
             ('p40_page_fPQ8x8k16', 'page-packed + page exp., 8 B filter (strict)', C['dark'], '--', 'D')]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.4))
    for ax, load in zip(axes, ('T8_W4', 'T16_W4')):
        for pre, lab, col, ls, mk in style:
            rs = sorted([r for r in rows if r['load'] == load and r['run'].startswith(pre)], key=lambda r: r['L'])
            if not rs: continue
            ax.plot([r['qps'] for r in rs], [100 * r['recall'] for r in rs], color=col, ls=ls, marker=mk, ms=5, lw=1.8,
                    label=lab)
        ax.set_xlabel('QPS (ladder_sim, calibrated NAND model)'); ax.set_ylabel('recall@10 (%)')
        ax.set_title(f'{load.replace("_", " ")}: recall vs throughput', loc='left', fontsize=10); ax.grid(alpha=0.25)
    axes[1].legend(frameon=False, fontsize=7.5, loc='upper right')
    fig.text(0.99, 0.005, 'local SIFT100M R64 graph, W=4, L = 50/70/100/150, 300 queries; host gap scaled by nodes expanded '
             'per read; extra channel transfer not modelled', ha='right', fontsize=7, color='#666')
    fig.tight_layout(rect=(0, 0.03, 1, 1)); fig.savefig(OUT / 'ssdpq_coloc_timing.png', dpi=140); plt.close(fig)


for f in (precision, footprint, coloc, autonomy, timing):
    f()
