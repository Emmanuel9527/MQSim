#!/usr/bin/env python3
"""Convert a diskann_block_trace.sh recording into an MQSim ASCII trace plus the real per-request record.

Steps (all read-only on the input directory):
  1. trace-cmd report -t (ns timestamps) -> block_rq_issue / block_rq_complete on the traced disk.
  2. Pair each read issue with its completion (FIFO per sector+length).
  3. Map whole-disk sectors -> LV sectors (partition start + dm-linear offset) -> index-file offsets (filefrag extents).
  4. Keep reads inside the disk index and inside the DiskANN search window (trace_stats.csv steady_clock ns;
     same CLOCK_MONOTONIC as trace-cmd -C mono).
  5. Cross-check the 4 KiB sector multiset against DiskANN's own trace_access.csv.

Outputs in <out>:
  mqsim_trace.txt   MQSim ASCII trace: "time_ns device lba_512 size_512 type" (type 1 = read), time relative to the
                    first kept issue, LBA = offset inside the index file (preserves DiskANN's file layout; the real
                    host LBA layout is irrelevant to the SSD's FTL placement, which is unknown anyway).
  real_requests.csv issue_ns,complete_ns,file_lba,sectors (absolute CLOCK_MONOTONIC ns)
  summary.json      counts, filters, cross-check and real latency statistics
"""
import argparse, bisect, collections, csv, json, re, subprocess, sys
from pathlib import Path

LINE = re.compile(r'\s(\d+\.\d+):\s+block_rq_(issue|complete):\s+(\d+),(\d+)\s+(\S+)\s+(?:(\d+)\s+)?\(\)\s+(\d+)\s+\+\s+(\d+)')


def pct(sorted_vals, p):
    if not sorted_vals: return None
    k = min(len(sorted_vals) - 1, max(0, round(p / 100 * (len(sorted_vals) - 1))))
    return sorted_vals[k]


def lat_stats(lat_us):
    s = sorted(lat_us)
    return {'n': len(s), 'mean_us': sum(s) / len(s) if s else None,
            **{f'p{p}_us': pct(s, p) for p in (50, 90, 99, 99.9)}, 'max_us': s[-1] if s else None}


def load_layout(d):
    lay = d / 'layout'
    majmin = {}
    for line in (lay / 'lsblk.txt').read_text().splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and re.fullmatch(r'\d+:\d+', parts[1]):
            majmin[parts[1]] = re.sub(r'^[^A-Za-z0-9]+', '', parts[0])
    disk_dev = tuple(int(x) for x in (lay / 'dev_majmin.txt').read_text().strip().split(':'))
    starts = {}
    for line in (lay / 'partitions.txt').read_text().splitlines():
        m = re.match(r'(\S+) start=(\d+) size=(\d+)', line)
        if m: starts[m.group(1)] = int(m.group(2))
    mount_src = (lay / 'mount.txt').read_text().splitlines()[1].split()[0]
    lv_name = Path(mount_src).name
    table = None
    for line in (lay / 'dmsetup_table.txt').read_text().splitlines():
        name, rest = line.split(':', 1)
        if name.strip() == lv_name: table = rest.split()
    if table is None or len(table) != 5 or table[2] != 'linear':
        raise SystemExit(f'Expected one dm-linear segment for {lv_name}, got {table}')
    target_part = majmin[table[3]]
    base = starts[target_part] + int(table[4])  # whole-disk sector of LV sector 0
    extents = []  # (lv_start, lv_end_exclusive, file_start) in 512 B sectors
    for line in (lay / 'disk_index.extents.before.txt').read_text().splitlines():
        m = re.match(r'\s*\d+:\s+(\d+)\.\.\s*(\d+):\s+(\d+)\.\.\s*(\d+):', line)
        if m:
            l0, l1, p0, p1 = map(int, m.groups())
            extents.append((p0, p1 + 1, l0))
    extents.sort()
    return disk_dev, base, extents, {'lv': lv_name, 'dm_target': target_part, 'lv_base_disk_sector': base,
                                     'extents': len(extents)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('trace_dir', type=Path)
    ap.add_argument('--out', type=Path, help='default: <trace_dir>/mqsim_replay')
    a = ap.parse_args()
    d = a.trace_dir.resolve(); out = (a.out or d / 'mqsim_replay').resolve()
    if out.exists(): raise SystemExit(f'Refusing to overwrite {out}')
    disk_dev, base, extents, layout_info = load_layout(d)
    if (d / 'layout/disk_index.extents.before.txt').read_text() != (d / 'layout/disk_index.extents.after.txt').read_text():
        raise SystemExit('Index extents changed during the run')
    ext_starts = [e[0] for e in extents]

    # DiskANN search window and its own sector accesses
    t_begin, t_end = None, None
    with open(d / 'trace_stats.csv') as f:
        for r in csv.DictReader(f):
            b, e = int(r['iteration_begin_ns']), int(r['iteration_end_ns'])
            if b: t_begin = b if t_begin is None else min(t_begin, b)
            if e: t_end = e if t_end is None else max(t_end, e)
    diskann_sectors = collections.Counter()
    with open(d / 'trace_access.csv') as f:
        for r in csv.DictReader(f):
            diskann_sectors[int(r['sector_id'])] += 1

    rep = subprocess.run(['trace-cmd', 'report', '-t', '-i', str(d / 'block.dat')], capture_output=True, text=True, check=True)
    pending = collections.defaultdict(collections.deque)
    reqs, counts = [], collections.Counter()
    for line in rep.stdout.splitlines():
        m = LINE.search(line)
        if not m: continue
        ts, kind, maj, mnr, rwbs, _bytes, sector, n = m.groups()
        if (int(maj), int(mnr)) != disk_dev: counts['other_device'] += 1; continue
        if 'R' not in rwbs or 'W' in rwbs: counts[f'{kind}_not_read'] += 1; continue
        ns = int(ts.replace('.', ''))  # seconds.nanoseconds -> ns
        key = (int(sector), int(n))
        if kind == 'issue':
            pending[key].append(ns)
        elif pending[key]:
            reqs.append((pending[key].popleft(), ns, key[0], key[1], rwbs))
        else:
            counts['complete_without_issue'] += 1
    counts['issue_without_complete'] = sum(len(v) for v in pending.values())

    kept, real_lat = [], []
    for issue, comp, sector, n, rwbs in sorted(reqs):
        lv = sector - base
        i = bisect.bisect_right(ext_starts, lv) - 1
        if i < 0 or not (extents[i][0] <= lv and lv + n <= extents[i][1]):
            counts['read_outside_index'] += 1; continue
        if not (t_begin <= issue <= t_end):
            counts['index_read_outside_search_window'] += 1; continue
        file_lba = extents[i][2] + (lv - extents[i][0])
        kept.append((issue, comp, file_lba, n))
        real_lat.append((comp - issue) / 1000)
    if not kept: raise SystemExit('No requests kept')

    block_sectors = collections.Counter()
    for _, _, lba, n in kept:
        if lba % 8 or n % 8: counts['not_4k_aligned'] += 1
        for s in range(lba // 8, (lba + n + 7) // 8): block_sectors[s] += 1
    overlap = sum((block_sectors & diskann_sectors).values())

    out.mkdir(parents=True)
    t0 = kept[0][0]
    with open(out / 'mqsim_trace.txt', 'w') as f:
        for issue, _, lba, n in kept: f.write(f'{issue - t0} 0 {lba} {n} 1\n')
    with open(out / 'real_requests.csv', 'w') as f:
        f.write('issue_ns,complete_ns,file_lba,sectors\n')
        for r in kept: f.write(','.join(map(str, r)) + '\n')
    span_s = (kept[-1][0] - t0) / 1e9
    busy = sum((c - i) for i, c, _, _ in kept) / 1e9
    summary = {
        'trace_dir': str(d), 'layout': layout_info,
        'search_window_ns': [t_begin, t_end], 'search_window_s': (t_end - t_begin) / 1e9,
        'paired_reads': len(reqs), 'kept_reads': len(kept), 'filters': dict(counts),
        'size_histogram_sectors': dict(collections.Counter(n for *_, n in kept)),
        'unique_4k_sectors': len(block_sectors),
        'diskann_trace_access_reads': sum(diskann_sectors.values()),
        'block_vs_diskann_sector_multiset_overlap': overlap,
        'issue_span_s': span_s, 'mean_iops': len(kept) / span_s,
        'mean_outstanding': busy / span_s,
        'real_latency': lat_stats(real_lat),
        'file_lba_range': [min(r[2] for r in kept), max(r[2] + r[3] for r in kept)],
    }
    (out / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
