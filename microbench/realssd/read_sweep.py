#!/usr/bin/env python3
"""Read-only fio calibration baseline. No SSD/MQSim parameters are modified."""
import argparse
import csv
import hashlib
import json
import os
import random
import shutil
import stat
import subprocess
import threading
import time
import tempfile
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_FILE = '/home/emmanuel/projects/data/sift100m_diskann_indexes/sift100m_l2_R64_L100_pq0_block_shuffle_disk.index'


def capture(argv):
    p = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return {'argv': argv, 'returncode': p.returncode, 'output': p.stdout.strip()}


def text_file(path):
    try:
        return Path(path).read_text().strip()
    except OSError as exc:
        return f'unavailable: {exc}'


def job_text(args, qd, seed, output):
    return f'''[global]
ioengine=libaio
direct=1
rw=randread
bs={args.block_kib * 1024}
filename={args.file}
allow_file_create=0
size={args.size_gib * 1024**3}
offset=0
numjobs={args.jobs}
thread=1
iodepth={qd // args.jobs}
iodepth_low={qd // args.jobs}
{'group_reporting=1' if args.jobs > 1 else ''}
iodepth_batch_submit=1
iodepth_batch_complete_min=1
iodepth_batch_complete_max=1
time_based=1
runtime={args.runtime}
ramp_time={args.ramp}
randrepeat=1
randseed={seed}
random_distribution=random
norandommap=1
invalidate=0
lat_percentiles=1
clat_percentiles=1
percentile_list=50:95:99:99.9
write_lat_log={output}/latency
write_iops_log={output}/iops
log_avg_msec=1000
log_unix_epoch=1
{f'cpus_allowed={args.cpu}' if args.cpu is not None else ''}
[read_qd{qd}]
'''


def summarize(doc, label, qd, repeat):
    jobs = doc['jobs']
    if len(jobs) != 1 or jobs[0]['error'] != 0:
        raise ValueError(f'{label}: expected one successful fio job')
    job = jobs[0]
    if job['write']['io_bytes'] or job.get('trim', {}).get('io_bytes', 0):
        raise ValueError('Unexpected write/trim traffic')
    read = job['read']
    if read['total_ios'] == 0:
        raise ValueError('No reads completed')
    row = dict(run=label, repeat=repeat, requested_qd=qd, iops=read['iops'],
               bandwidth_mib_s=read['bw_bytes'] / 1024**2, total_ios=read['total_ios'],
               measured_runtime_ms=read['runtime'], fio_user_cpu_pct=job['usr_cpu'],
               fio_system_cpu_pct=job['sys_cpu'], context_switches=job['ctx'])
    for kind in ('slat', 'clat', 'lat'):
        data = read[kind + '_ns']
        row[kind + '_mean_us'] = data['mean'] / 1000
        for percentile in (50, 95, 99, 99.9):
            value = data.get('percentile', {}).get(f'{percentile:.6f}')
            row[f'{kind}_p{percentile}_us'] = value / 1000 if value is not None else None
    # fio's sampled depth histogram is not a time-weighted device queue length.
    row['fio_depth_histogram_pct'] = json.dumps(job['iodepth_level'], sort_keys=True)
    return row


def archive_results(source, root):
    """Copy after measurement; publish only a verified complete copy. Keep tmpfs."""
    root.mkdir(parents=True, exist_ok=True)
    destination = root / source.name
    if destination.exists():
        raise FileExistsError(f'Refusing to overwrite archive: {destination}')
    staging = Path(tempfile.mkdtemp(prefix='.' + source.name + '.partial-', dir=root))
    # A failed copy remains explicitly partial for diagnosis; source is untouched.
    shutil.copytree(source, staging, dirs_exist_ok=True)
    checksums = {}
    for path in sorted(source.rglob('*')):
        if path.is_file():
            relative = path.relative_to(source)
            def digest(filename):
                with filename.open('rb') as stream:
                    return hashlib.file_digest(stream, 'sha256').hexdigest()
            original = digest(path)
            if digest(staging / relative) != original:
                raise RuntimeError(f'Archive verification failed: {relative}')
            checksums[str(relative)] = original
    (staging / 'archive_manifest.json').write_text(json.dumps({
        'source_tmpfs': str(source), 'archived_wall_time_ns': time.time_ns(),
        'sha256': checksums}, indent=2))
    staging.rename(destination)
    return destination


def monitor(stop, destination, device):
    """Raw counters with paired clocks; includes other activity on the device."""
    temps = sorted(p for h in Path('/sys/class/hwmon').glob('hwmon*')
                   if text_file(h / 'name') == 'nvme' for p in h.glob('temp*_input'))
    with destination.open('w') as stream:
        while not stop.is_set():
            record = {'wall_time_ns': time.time_ns(), 'monotonic_ns': time.monotonic_ns(),
                      'device': device, 'block_stat': text_file(f'/sys/class/block/{device}/stat'),
                      'cpu_stat': text_file('/proc/stat'),
                      'meminfo': text_file('/proc/meminfo'),
                      'temperatures_millicelsius': {str(p): text_file(p) for p in temps}}
            stream.write(json.dumps(record) + '\n')
            stream.flush()
            stop.wait(1)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--file', type=Path, default=Path(DEFAULT_FILE))
    p.add_argument('--device', default='nvme0n1', help='Whole device for monitoring; must back the input file')
    p.add_argument('--out', type=Path, help='New output directory on tmpfs')
    p.add_argument('--archive-root', type=Path,
                   default=Path(__file__).resolve().parents[2] / 'microbench' / 'results',
                   help='Persistent results parent; copied only after all measurement stops')
    p.add_argument('--qds', nargs='+', type=int, default=[1, 2, 4, 8, 16, 32, 64, 128])
    p.add_argument('--repeats', type=int, default=3)
    p.add_argument('--runtime', type=int, default=30, help='Measured seconds per run')
    p.add_argument('--ramp', type=int, default=10, help='Unmeasured warmup seconds per run')
    p.add_argument('--size-gib', type=int, default=32)
    p.add_argument('--block-kib', type=int, default=4)
    p.add_argument('--jobs', type=int, default=1, help='fio jobs sharing each total QD (QD must divide evenly)')
    p.add_argument('--seed', type=int, default=20261112)
    p.add_argument('--cpu', type=int, help='Optional fixed logical CPU; default leaves host policy unchanged')
    p.add_argument('--run', action='store_true', help='Execute; without this flag only preflight and plan')
    return p


def main():
    args = parser().parse_args()
    args.file = args.file.resolve(strict=True)
    if any(x <= 0 for x in args.qds + [args.repeats, args.runtime, args.size_gib, args.block_kib]):
        raise SystemExit('QD, repetitions, runtime and sizes must be positive')
    if len(set(args.qds)) != len(args.qds) or args.ramp < 0:
        raise SystemExit('QDs must be unique; ramp must be nonnegative')
    if args.jobs < 1 or any(q % args.jobs for q in args.qds):
        raise SystemExit('Every QD must be a positive multiple of --jobs')
    if args.cpu is not None and args.cpu not in os.sched_getaffinity(0):
        raise SystemExit('CPU is not in this process allowed affinity set')
    if args.block_kib & (args.block_kib - 1):
        raise SystemExit('block-kib must be a power of two')
    info = args.file.stat()
    if not stat.S_ISREG(info.st_mode):
        raise SystemExit('Use an existing regular data file, not a raw device')
    if info.st_size < args.size_gib * 1024**3 or info.st_blocks * 512 < info.st_size:
        raise SystemExit('Input too small or apparently sparse; use an already populated file')
    if any(c in str(args.file) for c in '\n\r:;#\\'):
        raise SystemExit('Unsupported fio filename characters')
    fio = shutil.which('fio')
    if not fio:
        raise SystemExit('Install the fio I/O benchmark first')
    version = capture([fio, '--version'])
    if not version['output'].startswith('fio-'):
        raise SystemExit('This is not the expected fio I/O benchmark')
    mount = capture(['findmnt', '-n', '-o', 'SOURCE,FSTYPE,MAJ:MIN', '-T', str(args.file)])
    major_minor = f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}'
    sysdev = Path('/sys/dev/block') / major_minor
    def backed_by(node, depth=0):
        # Partition paths contain the whole-device name; LVM/dm devices list their backing devices in slaves/.
        node = node.resolve()
        if args.device in node.parts:
            return True
        return depth < 4 and any(backed_by(s, depth + 1) for s in (node / 'slaves').glob('*'))
    if not Path('/sys/class/block', args.device).exists() or not backed_by(sysdev):
        raise SystemExit(f'Input device {major_minor} is not backed by {args.device}; check mapping')
    schedule = []
    rng = random.Random(args.seed)
    for repeat in range(1, args.repeats + 1):
        order = args.qds[:]
        rng.shuffle(order)
        for qd in order:
            schedule.append({'repeat': repeat, 'qd': qd, 'seed': args.seed + repeat})
    print(json.dumps({'input': str(args.file), 'mount': mount, 'fio': version,
                      'schedule': schedule,
                      'nominal_seconds': len(schedule) * (args.runtime + args.ramp)}, indent=2))
    if not args.run:
        print('Preflight passed. No benchmark executed. Add --run to execute.')
        return
    out = args.out or Path('/dev/shm') / ('mqsim_read_sweep_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    out = out.absolute()
    if out.exists() or not out.parent.is_dir():
        raise SystemExit('Output must be a new directory with an existing parent')
    fs = capture(['findmnt', '-n', '-o', 'FSTYPE', '-T', str(out.parent)])
    if fs['output'] != 'tmpfs':
        raise SystemExit('Output parent must be tmpfs; no silent disk fallback')
    if any(c in str(out) for c in '\n\r:;#\\'):
        raise SystemExit('Unsupported fio output path characters')
    if shutil.disk_usage(out.parent).free < 1024**3:
        raise SystemExit('Need at least 1 GiB free tmpfs')
    mem = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    if int(mem['MemAvailable'].split()[0]) * 1024 < 2 * 1024**3:
        raise SystemExit('Need at least 2 GiB available RAM')
    out.mkdir()
    repo = Path(__file__).resolve().parents[2]
    config = repo / 'ssdconfig.xml'
    metadata = {'arguments': {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
                'schedule': schedule, 'input_size': info.st_size, 'input_mtime_ns': info.st_mtime_ns,
                'fio': version, 'uname': capture(['uname', '-a']),
                'git': capture(['git', '-C', str(repo), 'rev-parse', 'HEAD']),
                'git_status': capture(['git', '-C', str(repo), 'status', '--short']),
                'input_mount': mount, 'allowed_cpus': sorted(os.sched_getaffinity(0)),
                'lsblk': capture(['lsblk', '-J', '-o', 'NAME,MODEL,SERIAL,REV,SIZE,TRAN,MOUNTPOINTS']),
                'scheduler': text_file(f'/sys/class/block/{args.device}/queue/scheduler'),
                'nvme_firmware': text_file('/sys/class/nvme/nvme0/firmware_rev'),
                'clock_note': 'Events contain wall_time_ns and monotonic_ns; fio latency logs use epoch milliseconds.'}
    if config.exists():
        data = config.read_bytes()
        metadata['mqsim_config_sha256'] = hashlib.sha256(data).hexdigest()
        (out / 'ssdconfig.snapshot.xml').write_bytes(data)
    (out / 'metadata.json').write_text(json.dumps(metadata, indent=2))
    stop = threading.Event()
    observer = threading.Thread(target=monitor, args=(stop, out / 'system_samples.jsonl', args.device))
    observer.start()
    rows = []
    try:
        with (out / 'events.jsonl').open('w', buffering=1) as events:
            for item in schedule:
                label = f'r{item["repeat"]}_qd{item["qd"]}'
                run = out / label
                run.mkdir()
                job = run / 'job.fio'
                job.write_text(job_text(args, item['qd'], item['seed'], run))
                command = [fio, '--readonly', '--output-format=json+', f'--output={run / "fio.json"}', str(job)]
                (run / 'command.json').write_text(json.dumps(command, indent=2))
                print(f'Running {label}: {out}', flush=True)
                events.write(json.dumps(dict(event='fio_start', run=label, wall_time_ns=time.time_ns(), monotonic_ns=time.monotonic_ns())) + '\n')
                with (run / 'stderr.txt').open('w') as errors, (run / 'stdout.txt').open('w') as output:
                    process = subprocess.Popen(command, stdout=output, stderr=errors)
                    try:
                        code = process.wait()
                    except BaseException:
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                        raise
                events.write(json.dumps(dict(event='fio_end', run=label, returncode=code, wall_time_ns=time.time_ns(), monotonic_ns=time.monotonic_ns())) + '\n')
                if code:
                    raise RuntimeError(f'{label} failed ({code}); inspect {run}')
                row = summarize(json.loads((run / 'fio.json').read_text()), label, item['qd'], item['repeat'])
                rows.append(row)
                with (out / 'summary.csv').open('w', newline='') as target:
                    writer = csv.DictWriter(target, fieldnames=list(row))
                    writer.writeheader()
                    writer.writerows(rows)
        (out / 'COMPLETE').write_text(f'{len(rows)} successful runs\n')
    except BaseException as exc:
        (out / 'INCOMPLETE').write_text(f'{type(exc).__name__}: {exc}\n')
        raise
    finally:
        stop.set()
        observer.join()
        original_error = sys.exc_info()[0] is not None
        try:
            archived = archive_results(out, args.archive_root.resolve())
            print(f'Results saved: {archived}', flush=True)
        except Exception as exc:
            print(f'ARCHIVE FAILED: {exc}. Original results retained at {out}', file=sys.stderr)
            if not original_error:
                raise
    print(f'Complete. Verified persistent archive saved; tmpfs copy retained at {out}')


if __name__ == '__main__':
    main()
