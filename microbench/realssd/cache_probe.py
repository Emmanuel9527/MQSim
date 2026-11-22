#!/usr/bin/env python3
"""Controlled read-only address replay using fio, with per-I/O offset/latency."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import random
import shutil
import subprocess
import threading
import time
import tempfile
import statistics
import re

from read_sweep import DEFAULT_FILE, archive_results, capture, monitor, summarize, text_file


def write_trace(path, filename, offsets):
    with path.open('w') as f:
        f.write(f'fio version 2 iolog\n{filename} add\n{filename} open\n')
        for offset in offsets:
            f.write(f'{filename} read {offset} 4096\n')
        f.write(f'{filename} close\n')


def offsets_for(size_mib, count, seed, start=0):
    rng = random.Random(seed)
    return [start + rng.randrange(size_mib * 256) * 4096 for _ in range(count)]


def job(trace, run, qd, count, detailed):
    detail = f'''write_lat_log={run}/latency
log_avg_msec=0
log_offset=1
log_issue_time=1
log_entries={max(1024, count)}
log_unix_epoch=1
''' if detailed else ''
    return f'''[probe]
ioengine=libaio
direct=1
rw=read
allow_file_create=0
read_iolog={trace}
replay_no_stall=1
numjobs=1
thread=1
iodepth={qd}
iodepth_low={qd}
iodepth_batch_submit=1
iodepth_batch_complete_min=1
iodepth_batch_complete_max=1
invalidate=0
lat_percentiles=1
clat_percentiles=1
percentile_list=50:95:99:99.9
{detail}'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--file', type=Path, default=Path(DEFAULT_FILE))
    p.add_argument('--requests', type=int, default=65536)
    p.add_argument('--qd', type=int, default=64)
    p.add_argument('--sizes-mib', type=int, nargs='+', default=[256, 16384])
    p.add_argument('--repeats', type=int, default=2)
    p.add_argument('--run', action='store_true')
    a = p.parse_args()
    a.file = a.file.resolve(strict=True)
    if not 1024 <= a.requests <= 1048576 or a.qd < 1 or a.repeats < 1 or min(a.sizes_mib) < 1:
        raise SystemExit('Invalid positive QD/repeats/sizes, or requests outside 1024..1048576')
    if not a.file.is_file() or a.file.stat().st_size < 2 * max(a.sizes_mib) * 1024**2:
        raise SystemExit('Need an existing populated file spanning both disjoint regions')
    if any(c in str(a.file) for c in '\n\r :;#\\'):
        raise SystemExit('Unsupported fio trace path')
    st = a.file.stat()
    if st.st_blocks * 512 < st.st_size:
        raise SystemExit('Apparently sparse input rejected')
    backing = Path(f'/sys/dev/block/{st.st_dev >> 8}:{st.st_dev & 255}')
    # Use os.major/minor instead of assumptions about Linux dev_t encoding.
    import os
    backing = Path(f'/sys/dev/block/{os.major(st.st_dev)}:{os.minor(st.st_dev)}').resolve()
    if 'nvme0n1' not in backing.parts:
        raise SystemExit('This preset requires the verified nvme0n1 input device')
    plan = []
    rng = random.Random(20261112)
    for repeat in range(1, a.repeats + 1):
        pairs = [(size, condition) for size in a.sizes_mib for condition in ('same', 'seed', 'region')]
        rng.shuffle(pairs)
        for size, condition in pairs:
            seed = 10000 + repeat * 100 + size
            for phase in ('prime', 'probe'):
                plan.append(dict(label=f'r{repeat}_{size}MiB_{condition}_{phase}', repeat=repeat,
                                 size_mib=size, condition=condition, phase=phase, detailed=True,
                                 seed=seed + (1 if condition == 'seed' and phase == 'probe' else 0),
                                 offset=size * 1024**2 if condition == 'region' and phase == 'probe' else 0))
    # ABBA logging comparison: not cache reset; ordering/state caveats remain explicit.
    for i, detailed in enumerate([False, True, True, False]):
        plan.append(dict(label=f'overhead_{i}_{"on" if detailed else "off"}', repeat=0,
                         size_mib=max(a.sizes_mib), condition='logging', phase='probe', detailed=detailed,
                         seed=777, offset=0))
    print(json.dumps({'runs': len(plan), 'requests_per_run': a.requests, 'qd': a.qd,
                      'sizes_mib': a.sizes_mib, 'total_read_gib': len(plan)*a.requests*4096/1024**3}, indent=2))
    if not a.run:
        return
    if capture(['findmnt', '-n', '-o', 'FSTYPE', '-T', '/dev/shm'])['output'] != 'tmpfs':
        raise SystemExit('Output must be tmpfs')
    if shutil.disk_usage('/dev/shm').free < 4 * 1024**3:
        raise SystemExit('Need 4 GiB free tmpfs')
    out = Path(tempfile.mkdtemp(prefix='mqsim_cache_probe_', dir='/dev/shm'))
    repo = Path(__file__).resolve().parents[2]
    extent_before = capture(['filefrag', '-b512', '-v', str(a.file)])
    if extent_before['returncode']:
        raise SystemExit('filefrag failed')
    metadata = dict(arguments={k: str(v) if isinstance(v, Path) else v for k,v in vars(a).items()},
                    plan=plan, fio=capture(['fio','--version']), backing=str(backing),
                    partition_start_sector=text_file(backing/'start'), hmb_enabled=text_file('/sys/class/nvme/nvme0/hmb'),
                    model=text_file('/sys/class/nvme/nvme0/model'), firmware=text_file('/sys/class/nvme/nvme0/firmware_rev'),
                    note='No cache flush or HMB change. Prime is NOT cold. File offsets are not NAND addresses.')
    (out/'metadata.json').write_text(json.dumps(metadata,indent=2))
    (out/'extents_before.txt').write_text(extent_before['output'])
    stop=threading.Event();observer=threading.Thread(target=monitor,args=(stop,out/'system_samples.jsonl','nvme0n1'))
    observer.start();rows=[]
    try:
        with (out/'events.jsonl').open('w',buffering=1) as events:
            for i,item in enumerate(plan):
                run=out/item['label'];run.mkdir();trace=run/'requests.iolog'
                offsets=offsets_for(item['size_mib'],a.requests,item['seed'],item['offset'])
                write_trace(trace,a.file,offsets)
                (run/'job.fio').write_text(job(trace,run,a.qd,a.requests,item['detailed']))
                command=['fio','--readonly','--output-format=json+',f'--output={run/"fio.json"}',str(run/'job.fio')]
                (run/'command.json').write_text(json.dumps(command))
                print(f'{i+1}/{len(plan)} {item["label"]}: {out}',flush=True)
                events.write(json.dumps(dict(event='start',run=item['label'],wall_ns=time.time_ns(),monotonic_ns=time.monotonic_ns()))+'\n')
                with (run/'stderr.txt').open('w') as err, (run/'stdout.txt').open('w') as stdout:
                    proc=subprocess.Popen(command,stderr=err,stdout=stdout)
                    try:code=proc.wait()
                    except BaseException:
                        proc.terminate()
                        try:proc.wait(timeout=10)
                        except subprocess.TimeoutExpired:proc.kill();proc.wait()
                        raise
                events.write(json.dumps(dict(event='end',run=item['label'],wall_ns=time.time_ns(),monotonic_ns=time.monotonic_ns(),code=code))+'\n')
                if code:raise RuntimeError(f'fio failed: {run}')
                row=summarize(json.loads((run/'fio.json').read_text()),item['label'],a.qd,item['repeat'])
                if row['total_ios']!=a.requests:raise RuntimeError('Unexpected read count')
                row.update(condition=item['condition'],phase=item['phase'],size_mib=item['size_mib'],
                           seed=item['seed'],offset=item['offset'],detailed=item['detailed'])
                rows.append(row)
        (out/'extents_after.txt').write_text(capture(['filefrag','-b512','-v',str(a.file)])['output'])
        with (out/'summary.csv').open('w') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
        (out/'COMPLETE').write_text(f'{len(rows)} runs\n')
    except BaseException as exc:
        (out/'INCOMPLETE').write_text(repr(exc));raise
    finally:
        stop.set();observer.join()
        saved=archive_results(out,repo/'microbench'/'results')
        print(f'Archived: {saved}',flush=True)


if __name__=='__main__':main()
