# Long-term real-device baseline

```bash
cd /home/emmanuel/projects/MQSim
bash microbench/run_long_baseline.sh --check
bash microbench/run_long_baseline.sh
```

Defaults: existing SIFT100M index, first 32 GiB, direct 4 KiB random reads,
single libaio job, QDs 1/2/4/8/16/32/64/128, 3 shuffled repetitions,
30 s read warmup plus 300 s measurement per case. Total nominal time is
7,920 seconds (2 h 12 min), plus setup, archive verification and analysis.
No raw-device writes, cache clearing, HMB change, sleep inhibition or power
management changes. Keep the host awake and avoid concurrent storage benchmarks.
Every one-second fast/slow interval is retained in original fio logs.

RAM output is /dev/shm/mqsim_long_*. Persistent results are automatically copied
to microbench/results/<run>/ after all measurements finish, followed by
analysis. No per-case copies to the measured disk. Original JSON+ results,
system samples and provenance are retained. An interrupted sweep is incomplete;
it is not silently treated as a completed sweep. The runner already attempts
to archive caught interruptions; power loss/SIGKILL cannot be recovered this way.

Configuration overrides (same method, not equivalent experiments):

```bash
# One-hour-focused sweep: ~66 min; sparse QDs, not the full default curve.
QDS="1 8 32 128" bash microbench/run_long_baseline.sh

# Another independent day/time: different randomization seed.
SEED=20261113 bash microbench/run_long_baseline.sh
```

Analysis creates qd_curves.png/svg, report.md, per_run.csv, by_qd.csv,
device_intervals.csv, convergence.csv, iops_windows.csv and convergence.md.
Convergence calculations are approximate means over retained full-ish one-second
windows; omitted first/last/irregular windows are counted explicitly. These are
descriptive checks, not a statistical stationarity test. Whole-run fio JSON is
the authority for exact completed-count/runtime throughput. Pooled latency
averages require request-count weighting; per-run p99 values must not be averaged
to claim a pooled p99. No outlier filtering or ad hoc correction factors.

This first experiment measures long-term CLOSED-LOOP QD response. Uniform/burst
open-loop arrivals are a separate held-out test, to be designed after determining
the repeatable operating range. Matching this QD curve alone is not calibration
validation for burst response, and none of these scripts changes MQSim.
