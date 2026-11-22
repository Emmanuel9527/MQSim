#!/usr/bin/env python3
"""Merge realssd/state_probe.sh outputs: per-second fio IOPS vs temperature and host traffic on the disk."""
import csv
import glob
import json
import os
import statistics as st
import sys

out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results", "state_probe_20261114")

samples = list(csv.DictReader(open(os.path.join(out, "samples.csv"))))
for s in samples:
    s["t_ms"] = int(s["time_ns"]) // 1_000_000
for a, b in zip(samples, samples[1:]):
    dt = max(1e-3, (b["t_ms"] - a["t_ms"]) / 1000)
    b["rd_iops_disk"] = (int(b["reads_done"]) - int(a["reads_done"])) / dt
    b["wr_iops_disk"] = (int(b["writes_done"]) - int(a["writes_done"])) / dt
    b["wr_kib_s"] = (int(b["sectors_written"]) - int(a["sectors_written"])) * 512 / 1024 / dt


def nearest(t_ms):
    return min(samples[1:], key=lambda s: abs(s["t_ms"] - t_ms))


rows = []
for log in sorted(glob.glob(os.path.join(out, "iops_*.log"))):
    tag = os.path.basename(log)[5:].rsplit(".", 2)[0]
    for line in csv.reader(open(log)):
        t, v = int(line[0]), int(line[1])
        s = nearest(t)
        rows.append(dict(tag=tag, t_ms=t, iops=v, temp_c=int(s["temp1"]) / 1000,
                         wr_iops=s.get("wr_iops_disk", 0), wr_kib_s=s.get("wr_kib_s", 0)))

with open(os.path.join(out, "merged.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)

print("phase      sec  iops: min  med   max | temp_c min-max | host writes/s med max")
for tag in dict.fromkeys(r["tag"] for r in rows):
    rs = [r for r in rows if r["tag"] == tag]
    iops = [r["iops"] for r in rs]
    tc = [r["temp_c"] for r in rs]
    wr = [r["wr_iops"] for r in rs]
    print(f"{tag:9s} {len(rs):4d} {min(iops):9d} {st.median(iops):6.0f} {max(iops):6d} | "
          f"{min(tc):5.1f}-{max(tc):5.1f} | {st.median(wr):7.1f} {max(wr):7.1f}")
    # Time series compressed: iops(k) / temp / writes per 5 s.
    print("   " + " ".join(f"{r['iops']/1000:.0f}k/{r['temp_c']:.0f}C/{r['wr_iops']:.0f}w" for r in rs[::5]))

idle =[s for s in samples if s["phase"].startswith("idle") and "wr_iops_disk" in s]
if idle:
    print(f"\nidle phases: temp {min(int(s['temp1']) for s in idle)/1000:.1f}-"
          f"{max(int(s['temp1']) for s in idle)/1000:.1f}C, host writes/s median "
          f"{st.median(s['wr_iops_disk'] for s in idle):.1f}, max {max(s['wr_iops_disk'] for s in idle):.1f}")

keys = ["temperature", "thm_temp1_trans_count", "thm_temp2_trans_count", "thm_temp1_total_time",
        "thm_temp2_total_time", "warning_temp_time", "critical_comp_time", "data_units_written",
        "host_write_commands", "host_read_commands", "controller_busy_time"]
print("\nSMART snapshots:")
for f in sorted(glob.glob(os.path.join(out, "smart_*.json")), key=os.path.getmtime):
    d = json.load(open(f))
    print(os.path.basename(f)[6:-5].ljust(16), " ".join(f"{k}={d.get(k)}" for k in keys if k in d))
