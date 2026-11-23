#!/usr/bin/env python3
"""Iso-recall read savings of graphsim runs (functional, no timing) against a baseline configuration.

Runs are graphsim --out JSONs named <config>_L<L>.json in one or more directories. For each run, the baseline's reads
at the same recall are linearly interpolated on the baseline's recall curve (its L sweep); '-' outside its range.
Usage: ssdpq/isorecall_reads.py --base far1.2 <dir> [<dir> ...] [--only prefix ...]
"""
import argparse, json
from collections import defaultdict
from pathlib import Path
import numpy as np

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument('dirs', nargs='+', type=Path); ap.add_argument('--base', required=True)
ap.add_argument('--only', nargs='*', default=[], help='configs to print (default: all)')
a = ap.parse_args()
cfg = defaultdict(list)
for d in a.dirs:
    for f in d.glob('*_L*.json'):
        m = json.load(open(f)); c = f.stem.rsplit('_L', 1)[0]
        if any(r['L'] == m['L'] for r in cfg[c]): continue
        cfg[c].append(dict(L=m['L'], **m['mean']))
b = sorted(cfg[a.base], key=lambda r: r['recall'])
br = np.array([r['recall'] for r in b]); bn = np.array([r['reads'] for r in b])
print(f"baseline {a.base}: recall {br[0]:.4f}..{br[-1]:.4f}")
print(f"{'config':34s} {'L':>4s} {'recall':>7s} {'reads':>7s} {'skip':>6s} {'late':>6s} {'base reads':>10s} {'iso':>7s}")
for c in sorted(cfg):
    if a.only and not any(c.startswith(o) for o in a.only) and c != a.base: continue
    for r in sorted(cfg[c], key=lambda r: r['L']):
        n0 = float(np.interp(r['recall'], br, bn)) if br[0] <= r['recall'] <= br[-1] else None
        bs = f'{n0:.2f}' if n0 else '-'; iso = f"{r['reads'] / n0 - 1:+.1%}" if n0 else '-'
        print(f"{c:34s} {r['L']:4d} {r['recall']:7.4f} {r['reads']:7.2f} {r.get('skipped', 0):6.2f} {r.get('late_skipped', 0):6.2f} "
              f"{bs:>10s} {iso:>7s}")
