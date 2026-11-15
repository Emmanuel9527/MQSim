#!/usr/bin/env python3
"""Build an MQSim closed-loop replay schedule from a diskann_block_trace.sh recording.

Needs <trace_dir>/mqsim_replay/real_requests.csv (from diskann_trace_to_mqsim.py) plus DiskANN's trace_stats.csv and
trace_access.csv. Every DiskANN round (one beam-search iteration of one query on one worker) is matched to the block
requests it issued: same 4 KiB sectors, issued inside [read_begin_ns, read_end_ns] of that round.

Per round the schedule keeps only host-side times measured on the real system:
  offset of each read  = block issue - read_begin           (submission path inside the round)
  post                 = read_end - last block completion   (completion path back to DiskANN)
  gap before the round = read_begin - previous round's read_end on the same worker (search compute, next query setup)
Device time is left to the simulator, so later rounds move when the simulated SSD is faster or slower.

Outputs in <trace_dir>/closed_loop/: schedule.txt (format in IO_Flow_Closed_Loop_Replay.h), real_rounds.csv, summary.json
"""
import argparse, bisect, collections, csv, json
from pathlib import Path
import numpy as np


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('trace_dir', type=Path)
    a = ap.parse_args()
    d = a.trace_dir.resolve(); out = d / 'closed_loop'
    if out.exists(): raise SystemExit(f'Refusing to overwrite {out}')

    req = np.loadtxt(d / 'mqsim_replay/real_requests.csv', delimiter=',', skiprows=1, dtype=np.int64)
    order = np.argsort(req[:, 0], kind='stable'); req = req[order]
    by_sector = collections.defaultdict(list)  # 4 KiB sector -> request indices in issue order
    for i, (_, _, lba, n) in enumerate(req):
        for s in range(lba // 8, (lba + n + 7) // 8): by_sector[s].append(i)
    issue_by_sector = {s: [int(req[i, 0]) for i in v] for s, v in by_sector.items()}

    access = collections.defaultdict(list)
    with open(d / 'trace_access.csv') as f:
        for r in csv.DictReader(f): access[(int(r['query_id']), int(r['iteration']))].append(int(r['sector_id']))

    rounds = []
    with open(d / 'trace_stats.csv') as f:
        for r in csv.DictReader(f):
            rb, re_ = int(r['read_begin_ns']), int(r['read_end_ns'])
            key = (int(r['query_id']), int(r['iteration']))
            if rb == 0 or re_ == 0 or not access.get(key): continue  # no read in this iteration: its time falls into the gap
            rounds.append((int(r['worker_id']), rb, re_, key))
    rounds.sort(key=lambda x: x[1])

    used = np.zeros(len(req), dtype=bool)
    stats = collections.Counter()
    per_round = []
    for worker, rb, re_, key in rounds:
        mine = []
        for s in access[key]:
            lst, times = by_sector.get(s, []), issue_by_sector.get(s, [])
            j = bisect.bisect_left(times, rb)
            found, reuse = None, None
            while j < len(times) and times[j] <= re_:
                i = lst[j]
                if not used[i]: found = i; break           # a separate request (also covers duplicate sector reads)
                if reuse is None and i in mine: reuse = i  # same request already taken by this round (kernel merge)
                j += 1
            if found is None and reuse is not None: found = reuse; stats['merged_reuse'] += 1
            if found is None: stats['unmatched_sector_reads'] += 1; continue
            if found not in mine: used[found] = True; mine.append(found)
        if not mine: stats['rounds_without_requests'] += 1; continue
        mine.sort(key=lambda i: req[i, 0])
        last_c = int(req[mine, 1].max())
        per_round.append((worker, key[0], key[1], rb, re_, mine, last_c))
    stats['unused_block_requests'] = int((~used).sum())

    t0 = min(r[3] for r in per_round)
    by_worker = collections.defaultdict(list)
    for r in per_round: by_worker[r[0]].append(r)
    out.mkdir(parents=True)
    neg = collections.Counter()
    with open(out / 'schedule.txt', 'w') as sch, open(out / 'real_rounds.csv', 'w') as rr:
        sch.write('# worker query gap_ns post_ns n [off_ns lba sectors]...\n')
        rr.write('worker,query,iteration,read_begin_ns,read_end_ns,first_issue_ns,last_complete_ns,n\n')
        for w in sorted(by_worker):
            prev_end = t0
            for worker, q, it, rb, re_, mine, last_c in by_worker[w]:
                gap = rb - prev_end
                post = re_ - last_c
                if gap < 0: neg['gap'] += 1; gap = 0
                if post < 0: neg['post'] += 1; post = 0
                parts = []
                for i in mine:
                    off = int(req[i, 0]) - rb
                    if off < 0: neg['offset'] += 1; off = 0
                    parts.append(f'{off} {int(req[i, 2])} {int(req[i, 3])}')
                sch.write(f'{w} {q} {gap} {post} {len(mine)} ' + ' '.join(parts) + '\n')
                rr.write(f'{w},{q},{it},{rb},{re_},{int(req[mine, 0].min())},{last_c},{len(mine)}\n')
                prev_end = re_
    io = np.array([r[4] - r[3] for r in per_round]) / 1e3
    post = np.array([r[4] - r[6] for r in per_round]) / 1e3
    pre = np.array([int(req[r[5], 0].min()) - r[3] for r in per_round]) / 1e3
    summary = {
        'trace_dir': str(d), 'workers': len(by_worker), 'rounds': len(per_round),
        'matched_block_requests': int(used.sum()), 'block_requests': int(len(req)), 'matching': dict(stats),
        'clamped_negative': dict(neg),
        'real_span_s': (max(r[4] for r in per_round) - t0) / 1e9,
        'real_round_io_us': {'mean': float(io.mean()), 'p50': float(np.percentile(io, 50)), 'p99': float(np.percentile(io, 99))},
        'host_pre_issue_us': {'mean': float(pre.mean()), 'p50': float(np.percentile(pre, 50)), 'p99': float(np.percentile(pre, 99))},
        'host_post_complete_us': {'mean': float(post.mean()), 'p50': float(np.percentile(post, 50)), 'p99': float(np.percentile(post, 99))},
    }
    (out / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
