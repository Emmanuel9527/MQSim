#!/usr/bin/env python3
"""Build fio v2 iologs that replay the read addresses of a DiskANN recording against the same index file.

Input: <trace_dir>/mqsim_replay/real_requests.csv (issue-ordered block requests, offsets inside the index file).
Patterns:
  diskann   the recorded requests in their real issue order
  shuffled  the same requests in random order (same hot sectors, temporal structure removed)
  uniform   the same number of 4 KiB reads at uniformly random aligned offsets of the index file (no hot spots)
Each pattern is split round-robin into --jobs files so several fio jobs can replay it concurrently.
"""
import argparse, csv, random
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('trace_dir', type=Path)
    ap.add_argument('index_file', type=Path)
    ap.add_argument('out', type=Path)
    ap.add_argument('--jobs', type=int, default=4)
    ap.add_argument('--seed', type=int, default=20261115)
    a = ap.parse_args()
    size = a.index_file.stat().st_size
    reqs = []
    with open(a.trace_dir / 'mqsim_replay/real_requests.csv') as f:
        for r in csv.DictReader(f):
            off, ln = int(r['file_lba']) * 512, int(r['sectors']) * 512
            if off % 4096 or ln % 4096 or off + ln > size:
                raise SystemExit(f'Unaligned or out-of-file request: {r}')
            reqs.append((off, ln))
    rng = random.Random(a.seed)
    shuffled = reqs[:]; rng.shuffle(shuffled)
    pages = size // 4096
    uniform = [(rng.randrange(pages) * 4096, 4096) for _ in reqs]
    a.out.mkdir(parents=True, exist_ok=False)
    name = str(a.index_file)
    for pattern, seq in (('diskann', reqs), ('shuffled', shuffled), ('uniform', uniform)):
        for j in range(a.jobs):
            with open(a.out / f'{pattern}_job{j}.iolog', 'w') as f:
                f.write(f'fio version 2 iolog\n{name} add\n{name} open\n')
                f.writelines(f'{name} read {off} {ln}\n' for off, ln in seq[j::a.jobs])
                f.write(f'{name} close\n')
    print(f'{len(reqs)} requests per pattern, {a.jobs} jobs, index {size} bytes -> {a.out}')


if __name__ == '__main__':
    main()
