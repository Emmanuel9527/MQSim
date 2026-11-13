#!/usr/bin/env python3
"""Summarize gap_probe.sh: per idle gap, fraction of busy 100 ms windows in the fast state."""
import csv
import glob
import json
import os
import re
import statistics as st
import sys

out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "results", "gap_probe_20261114")
FAST = float(os.environ.get("FAST_IOPS", 50000))  # QD16: slow ~18k, fast ~110k+


def key(path):
    m = re.search(r"(apst_\w+?)_(warm|gap(\d+))_iops", os.path.basename(path))
    return (m.group(1), -1 if m.group(2) == "warm" else int(m.group(3)))


print(f"fast threshold {FAST:.0f} IOPS per 100 ms window")
print("mode       gap_ms  busy_win  fast_frac  busy_med_iops  fast_after_gap  fio_avg_iops")
fio = {}
for j in glob.glob(os.path.join(out, "apst_*.json")):
    for job in json.load(open(j))["jobs"]:
        fio[job["jobname"]] = job["read"]["iops"]
for log in sorted(glob.glob(os.path.join(out, "apst_*_iops.*.log")), key=key):
    mode, gap = key(log)
    rows = [(int(r[0]), int(r[1])) for r in csv.reader(open(log))]
    busy = [(t, v) for t, v in rows if v > 1000]
    if not busy:
        continue
    vals = [v for _, v in busy]
    # first busy window following an idle hole of >= 100 ms (visible only for gaps >= ~100 ms)
    after = [v for (t0, _), (t1, v) in zip(busy, busy[1:]) if t1 - t0 >= 150]
    aft = f"{sum(v > FAST for v in after)}/{len(after)}" if after else "-"
    name = f"{mode}_{'warm' if gap < 0 else f'gap{gap}'}"
    print(f"{mode:9s} {('warm' if gap < 0 else gap):>7} {len(vals):9d} {sum(v > FAST for v in vals)/len(vals):9.2f} "
          f"{st.median(vals):14.0f} {aft:>15} {fio.get(name, float('nan')):13.0f}")
    print("    ", " ".join(f"{v//1000}" for _, v in busy[:60]))
