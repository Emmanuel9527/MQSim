# Address/cache hypothesis probe

Run from MQSim:

```bash
python3 microbench/realssd/cache_probe.py             # inspect plan only
python3 microbench/realssd/cache_probe.py --run       # read-only real-device test
python3 microbench/realssd/analyze_cache_probe.py microbench/results/<mqsim_cache_probe_name>
```

The runner uses fio's v2 read_iolog and replay_no_stall, not a custom I/O engine.
Explicit read-only traces fix exact counts and addresses. QD=64, 65,536 reads per
run, 4 KiB, worksets 256 MiB/16 GiB, two randomized repetitions. Each pair first
reads region A, then repeats identical A, changes the random sequence within A,
or translates the identical sequence into nonoverlapping B. Pair order is
randomized; sequence is shared among the prime runs of one size/repetition.
Four final ABBA runs compare detailed logging off/on/on/off. No cache reset,
TRIM, data writing, HMB changes or MQSim edits are performed. HMB enabled is
recorded as context, not evidence of hits. Prime is NOT a cold-cache baseline.

Logs and input traces are on tmpfs; the entire experiment is archived afterward.
Detailed logging uses fio write_lat_log, log_offset, log_issue_time and preallocated
log_entries. fio JSON+ contains all completed-read summary statistics. The
analyzer verifies actual submission offsets against the entire planned sequence,
then joins clat records by (issue timestamp, offset), not by completion order.
The local fio build omits QD-1 trailing completion latency records in the observed
fixed-count tests (slat logs and JSON completed counts are complete). Missing
records are reported and excluded, never reconstructed or fabricated.

filefrag -b512 extents are saved before/after and must remain unchanged. For
this direct ext4 partition mapping, derived device LBA = physical extent sector
+ within-extent sector + partition start. This is not an observed NVMe command
trace, not a physical NAND address, and not evidence about channel placement.

Outputs under address_analysis include paired comparisons, unique/repeated
address fractions, sequence hashes, latency by reuse class, per-request offset/
issue time/derived LBA/clat, issue-time windows and explicit coverage warnings.
Repeated-in-run means an offset was submitted earlier; it does not establish
that earlier data has completed or is present in a cache. Request-gap is an
index gap, not distinct-page stack distance. First-in-run is not first-ever.

Fast runs may finish in under one second. This probe tests controlled address
relationships, not long-term throughput. Logging itself can perturb performance;
the ABBA control has limited replication and cannot completely separate overhead
from device-state drift. Changing seeds does not eliminate overlaps: at a small
working set the overlap can be large. Measured overlap is reported explicitly.

References: fio documentation sections write_lat_log, log_offset, log_issue_time,
read_iolog, replay_no_stall: https://fio.readthedocs.io/en/latest/fio_doc.html
