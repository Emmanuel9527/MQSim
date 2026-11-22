# Analyze completed real-device runs

From the MQSim repository:

```bash
python3 microbench/realssd/analyze_read_sweep.py microbench/results/<run-name>
```

To wait for the archive of a currently running sweep, use its exact printed
run-directory name (not an older test run):

```bash
python3 microbench/realssd/analyze_read_sweep.py microbench/results/<run-name> --wait --timeout 3600
```

The archive only appears under its final name after copy verification. No new
I/O benchmark is started. This command does not touch the running fio process.
A failed or incomplete sweep is rejected. The default output is a new `analysis`
subdirectory. Existing analysis is not overwritten; use `--out` for another
analysis directory. Python matplotlib is required for plots.

Outputs:

- per_run.csv: statistics recomputed directly from original fio JSON.
- by_qd.csv: median/min/max across repetitions, and throughput ratios.
- device_intervals.csv: interval deltas of whole-device block counters.
- qd_curves.png and qd_curves.svg: all repetitions, median and min–max curves.
- report.md: result table, repeatability flags and interpretation limits.

The report checks archive SHA-256, expected run presence, fio errors and zero
writes. Short runs or fewer than three repeats are flagged. An IOPS max/min
ratio above 1.2 is a review heuristic only; no measurement is excluded. A median
of per-run p99 values is NOT a pooled p99. Device samples include other workloads
and setup/warmup; no automatic exact alignment to the measured fio phase is
claimed. The current report is a real-device baseline, not an MQSim calibration.
No configuration, raw measurements or MQSim source is changed by analysis.
