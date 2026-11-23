#!/usr/bin/env python3
"""Iso-recall gains of graphsim/ladder_coloc runs against a baseline configuration.

For every run (name = <config>_L<L>) and load, the baseline's QPS and reads at the same recall are linearly
interpolated on the baseline's recall curve (its L sweep); gain = run / baseline - 1 (QPS), run / baseline - 1 (reads).
Runs whose recall lies outside the baseline's recall range are reported as '-'.
Usage: ladder/isorecall_qps.py <timing.jsonl> [--base default] [--load T16_W4]
"""
import argparse, json
from collections import defaultdict
import numpy as np

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument('timing'); ap.add_argument('--base', default='default'); ap.add_argument('--load', default='T16_W4')
a = ap.parse_args()
rows = [json.loads(l) for l in open(a.timing)]
rows = [r for r in rows if r['load'] == a.load]
cfg = defaultdict(list)
for r in rows: cfg[r['run'].rsplit('_L', 1)[0]].append(r)
b = sorted(cfg[a.base], key=lambda r: r['recall'])
br = np.array([r['recall'] for r in b]); bq = np.array([r['qps'] for r in b]); bn = np.array([r['reads_mean'] for r in b])
def at(x, y, r): return float(np.interp(r, br, y)) if br[0] <= r <= br[-1] else None
print(f'{a.load}, baseline {a.base} (recall {br[0]:.4f}..{br[-1]:.4f})')
print(f"{'config':26s} {'L':>4s} {'recall':>7s} {'reads':>6s} {'QPS':>6s} {'reads iso':>9s} {'QPS iso':>8s} {'gap x':>6s}")
for c in sorted(cfg):
    for r in sorted(cfg[c], key=lambda r: r['L']):
        q0, n0 = at(br, bq, r['recall']), at(br, bn, r['recall'])
        f = lambda v, v0: f'{v / v0 - 1:+.1%}' if v0 else '-'
        print(f"{c:26s} {r['L']:4d} {r['recall']:7.4f} {r['reads_mean']:6.1f} {r['qps']:6.0f} {f(r['reads_mean'], n0):>9s} "
              f"{f(r['qps'], q0):>8s} {r['gap_scale']:6.2f}")
