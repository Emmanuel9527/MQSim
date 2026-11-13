#!/usr/bin/env python3
"""Plan/run read-only fio experiments; no I/O benchmark without --run."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from read_sweep import DEFAULT_FILE, archive_results, capture, monitor, summarize, text_file
from analyze_read_sweep import verify_archive, write_csv

REPO = Path(__file__).resolve().parent.parent
FAMILIES = ('size', 'size-qd', 'locality', 'jobs', 'arrival')


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--family', choices=(*FAMILIES, 'all', 'pipeline'), default='size')
    p.add_argument('--file', type=Path, default=Path(DEFAULT_FILE))
    p.add_argument('--device', default='nvme0n1')
    p.add_argument('--runtime', type=int, default=30)
    p.add_argument('--ramp', type=int, default=10)
    p.add_argument('--repeats', type=int, default=3)
    p.add_argument('--seed', type=int, default=20261113)
    p.add_argument('--sizes-kib', type=int, nargs='+', default=[4, 16, 64, 256, 1024])
    p.add_argument('--qds', type=int, nargs='+', default=[1, 8, 32, 128])
    p.add_argument('--working-sets-mib', type=int, nargs='+', default=[256, 4096, 32768])
    p.add_argument('--size-mib', type=int, default=32768)
    p.add_argument('--total-qd', type=int, default=32)
    p.add_argument('--jobs', type=int, nargs='+', default=[1, 2, 4, 8])
    p.add_argument('--rates', type=int, nargs='+', default=[2000, 10000, 20000])
    p.add_argument('--burst', type=int, default=8)
    p.add_argument('--batches', type=int, nargs='+', default=[16, 64, 256])
    p.add_argument('--paired-arrivals', action='store_true',
                   help='Arrival-only: shuffle rates, keep each smooth/burst pair adjacent, alternate order by repeat')
    p.add_argument('--arrival-qd', type=int, default=128)
    p.add_argument('--arrival-seconds', type=int, default=10)
    p.add_argument('--timeout', type=int, default=600, help='Per fio process timeout, including drain')
    p.add_argument('--ram-root', type=Path, default=Path('/dev/shm'))
    p.add_argument('--archive-root', type=Path, default=REPO/'microbench/results')
    p.add_argument('--cpu', type=int)
    p.add_argument('--run', action='store_true')
    p.add_argument('--analyze', type=Path, help='Analyze a complete suite archive; never runs fio')
    return p


def validate(a):
    if a.paired_arrivals and a.family != 'arrival':
        raise ValueError('--paired-arrivals requires --family arrival')
    positive = [a.runtime, a.repeats, a.size_mib, a.total_qd, a.burst,
                a.arrival_qd, a.arrival_seconds, a.timeout, *a.sizes_kib,
                *a.qds, *a.working_sets_mib, *a.jobs, *a.rates]
    if min(positive) < 1 or a.ramp < 0:
        raise ValueError('Sizes/counts/times must be positive; ramp may be zero')
    for values in (a.sizes_kib, a.qds, a.working_sets_mib, a.jobs, a.rates):
        if len(values) != len(set(values)):
            raise ValueError('Duplicate sweep values are not allowed')
    if any(n & (n-1) for n in a.sizes_kib) or min(a.sizes_kib) < 4:
        raise ValueError('Request sizes must be powers of two >= 4 KiB')
    if any(a.total_qd % n for n in a.jobs):
        raise ValueError('Each job count must divide total-qd')
    if a.burst > a.arrival_qd:
        raise ValueError('arrival-qd must be at least burst size')
    if any(r*a.arrival_seconds % a.burst for r in a.rates):
        raise ValueError('rate * arrival-seconds must be divisible by burst size')
    if max(a.rates)*a.arrival_seconds > 1000000:
        raise ValueError('At most 1,000,000 requests per arrival run; reduce duration/rate')
    if a.family == 'pipeline':
        if not a.batches or min(a.batches)<1 or len(set(a.batches))!=len(a.batches):
            raise ValueError('Batch sizes must be unique positive integers')
        if max(a.batches)>a.arrival_qd:
            raise ValueError('arrival-qd must accommodate the largest batch')
        if any(rate*a.arrival_seconds%b for rate in a.rates for b in a.batches):
            raise ValueError('Request count must be divisible by every batch size')
    if a.cpu is not None and a.cpu not in os.sched_getaffinity(0):
        raise ValueError('CPU not in allowed affinity set')


def make_plan(a):
    groups = FAMILIES if a.family == 'all' else (a.family,)
    cases = []
    def add(family, bs=4, qd=1, size=None, rw='randread', jobs=1, **kw):
        cases.append(dict(family=family, bs_kib=bs, per_job_qd=qd, jobs=jobs,
                          total_qd=qd*jobs, size_mib=size or a.size_mib, rw=rw, **kw))
    if 'pipeline' in groups:
        for rate in a.rates:
            for batch in a.batches:
                add('arrival', qd=a.arrival_qd, rate=rate, mode=f'batch{batch}',
                    batch_size=batch, requests=rate*a.arrival_seconds)
        for qd in a.qds:
            add('continuous', qd=qd)
    if 'size' in groups:
        for bs in a.sizes_kib: add('size', bs=bs)
    if 'size-qd' in groups:
        for bs in a.sizes_kib:
            for qd in a.qds: add('size-qd', bs=bs, qd=qd)
    if 'locality' in groups:
        for size in a.working_sets_mib:
            for rw in ('read', 'randread'):
                for qd in (1, 32): add('locality', size=size, rw=rw, qd=qd)
    if 'jobs' in groups:
        for jobs in a.jobs: add('jobs', qd=a.total_qd//jobs, jobs=jobs)
    if 'arrival' in groups:
        for rate in a.rates:
            for mode in ('smooth', 'burst'):
                add('arrival', qd=a.arrival_qd, rate=rate, mode=mode,
                    requests=rate*a.arrival_seconds)
    for index, case in enumerate(cases):
        case['case_id'] = f'{case["family"]}_{index:03d}'
        case['role'] = 'held_out_validation' if case['family']=='arrival' else 'calibration_candidate'
        if case['bs_kib']*1024 > case['size_mib']*1024**2:
            raise ValueError('Request larger than working set')
    plan=[]
    rng=random.Random(a.seed)
    for repeat in range(1,a.repeats+1):
        order=cases[:];rng.shuffle(order)
        if a.paired_arrivals:
            rates=a.rates[:];rng.shuffle(rates)
            modes=('smooth','burst') if repeat%2 else ('burst','smooth')
            order=[next(c for c in cases if c['rate']==rate and c['mode']==mode)
                   for rate in rates for mode in modes]
        for case in order:
            plan.append(dict(case, repeat=repeat, seed=a.seed+repeat,
                             label=f'r{repeat}_{case["case_id"]}'))
    return plan


def arrival_trace(path, filename, case, burst):
    """fio v3 timestamp unit is microseconds; first I/O at 1 us anchors replay."""
    burst=case.get('batch_size',burst)
    rng=random.Random(case['seed'])
    digest=hashlib.sha256()
    with path.open('w') as f:
        f.write(f'fio version 3 iolog\n0 {filename} add\n0 {filename} open\n')
        for i in range(case['requests']):
            offset=rng.randrange(case['size_mib']*256)*4096
            group=i if case['mode']=='smooth' else i//burst*burst
            due=1+group*1000000//case['rate']
            f.write(f'{due} {filename} read {offset} 4096\n')
            digest.update(f'{offset}\n'.encode())
        f.write(f'{due} {filename} close\n')
    return digest.hexdigest()


def job_text(a, case, run):
    opts=dict(ioengine='libaio', direct=1, rw=case['rw'], bs=case['bs_kib']*1024,
              filename=a.file, allow_file_create=0, size=case['size_mib']*1024**2,
              offset=0, numjobs=case['jobs'], thread=1, iodepth=case['per_job_qd'],
              iodepth_low=case['per_job_qd'], iodepth_batch_submit=1,
              iodepth_batch_complete_min=1, iodepth_batch_complete_max=1,
              invalidate=0, randrepeat=1, randseed=case['seed'], norandommap=1,
              lat_percentiles=1, clat_percentiles=1, percentile_list='50:95:99:99.9',
              write_lat_log=run/'latency', log_unix_epoch=1, per_job_logs=1)
    if a.cpu is not None: opts['cpus_allowed']=a.cpu
    if case['family']=='arrival':
        opts.update(read_iolog=run/'requests.iolog', replay_no_stall=0,
                    read_iolog_chunked=0, log_avg_msec=0, log_offset=1,
                    log_issue_time=1, log_entries=case['requests']+case['per_job_qd']+1024)
    else:
        opts.update(time_based=1, runtime=a.runtime, ramp_time=a.ramp,
                    log_avg_msec=1000, write_iops_log=run/'iops')
    return '[experiment]\n'+''.join(f'{k}={v}\n' for k,v in opts.items())


def extract(doc, case):
    if len(doc['jobs'])!=case['jobs']:
        raise ValueError('Unexpected fio job count; do not enable group_reporting')
    rows=[]
    for index, job in enumerate(doc['jobs']):
        row=summarize({'jobs':[job]}, case['label'], case['per_job_qd'], case['repeat'])
        row.update(case, job_index=index)
        rows.append(row)
    if case['family']=='arrival' and sum(r['total_ios'] for r in rows)!=case['requests']:
        raise ValueError('Arrival replay did not complete the exact planned request count')
    return rows


def audit_arrival(run, case, output):
    planned=[]
    for line in (run/'requests.iolog').read_text().splitlines():
        parts=line.split()
        if len(parts)==5 and parts[2]=='read': planned.append((int(parts[0]),int(parts[3])))
    files=list(run.glob('latency_slat.*.log'))
    if len(files)!=1: raise ValueError('Expected one submission log')
    with files[0].open() as stream: submitted=list(csv.reader(stream))
    if len(submitted)!=len(planned) or any(len(r)!=7 for r in submitted):
        raise ValueError('Incomplete or unsupported submission log; cannot validate arrivals')
    if [int(r[4]) for r in submitted]!=[p[1] for p in planned]:
        raise ValueError('fio logged offset order differs from planned trace')
    completions={}
    for file in run.glob('latency_clat.*.log'):
        with file.open() as stream:
            for r in csv.reader(stream):
                key=(int(r[6]),int(r[4]))
                if key in completions: raise ValueError('Ambiguous completion identity')
                completions[key]=float(r[1])/1000
    first=int(submitted[0][6]); rows=[]; keys=set()
    for i,(p,r) in enumerate(zip(planned,submitted)):
        issue=int(r[6]);key=(issue,int(r[4]))
        if key in keys: raise ValueError('Ambiguous submission identity')
        keys.add(key)
        relative_us=(issue-first)/1000
        due_us=p[0]-planned[0][0]
        rows.append(dict(request_index=i, offset=p[1], planned_relative_us=due_us,
                         fio_issue_ns=issue, issue_relative_us=relative_us,
                         relative_schedule_error_us=relative_us-due_us,
                         clat_us=completions.get(key)))
    if not set(completions).issubset(keys): raise ValueError('Unmatched completions')
    write_csv(output/f'{case["label"]}_arrivals.csv',rows)
    errors=sorted(r['relative_schedule_error_us'] for r in rows)
    return dict(run=case['label'], planned_requests=len(planned), logged_submissions=len(rows),
                logged_completions=len(completions), missing_completions=len(rows)-len(completions),
                relative_schedule_error_p50_us=errors[len(errors)//2],
                relative_schedule_error_p99_us=errors[min(len(errors)-1,int(len(errors)*.99))],
                relative_schedule_error_max_us=max(errors),
                issue_span_us=rows[-1]['issue_relative_us'],
                planned_span_us=rows[-1]['planned_relative_us'])


def analyze(root):
    manifest=verify_archive(root)
    meta=json.loads((root/'metadata.json').read_text())
    if meta.get('suite')!='calibration-v1': raise ValueError('Not a calibration suite archive')
    output=root/'analysis'
    if output.exists(): raise FileExistsError(f'Refusing to overwrite {output}')
    # Stage analysis so a failed audit does not leave a final-looking report.
    stage=Path(tempfile.mkdtemp(prefix='.analysis-',dir=root))
    jobs=[]; runs=[]; arrivals=[]; paired_addresses={}
    for case in meta['plan']:
        relative=f'{case["label"]}/fio.json'
        if relative not in manifest['sha256']: raise ValueError('Unverified fio result')
        run=root/case['label']
        rows=extract(json.loads((run/'fio.json').read_text()),case);jobs.extend(rows)
        count=sum(r['total_ios'] for r in rows)
        row=dict(case, total_ios=count, sum_job_iops=sum(r['iops'] for r in rows),
                 max_job_runtime_ms=max(r['measured_runtime_ms'] for r in rows))
        for kind in ('slat','clat','lat'):
            row[kind+'_weighted_mean_us']=sum(r[kind+'_mean_us']*r['total_ios'] for r in rows)/count
        runs.append(row)
        if case['family']=='arrival':
            for file in [run/'requests.iolog',*run.glob('latency_*.log')]:
                if str(file.relative_to(root)) not in manifest['sha256']:
                    raise ValueError('Unverified arrival trace/log')
            digest=hashlib.sha256()
            for line in (run/'requests.iolog').read_text().splitlines():
                fields=line.split()
                if len(fields)==5 and fields[2]=='read':
                    digest.update((fields[3]+'\n').encode())
            pair=(case['repeat'],case['rate'])
            actual=digest.hexdigest()
            if pair in paired_addresses and paired_addresses[pair]!=actual:
                raise ValueError('Smooth/burst address sequences differ')
            paired_addresses[pair]=actual
            arrivals.append(audit_arrival(run,case,stage))
    # Cases have family-specific fields. Normalize columns without dropping any.
    def output_csv(name, rows):
        if rows:
            fields=list(dict.fromkeys(k for row in rows for k in row))
            write_csv(stage/name,[{k:r.get(k) for k in fields} for r in rows])
    output_csv('per_job.csv',jobs);output_csv('per_run.csv',runs)
    output_csv('arrival_audit.csv',arrivals)
    lines=['# Calibration suite results','',
           'Archive checksums verified; metrics recomputed from original fio JSON+.',
           'No outliers removed. No MQSim parameters fitted or changed.','',
           '| Run | Family | KiB | Total requested QD | Jobs | Sum job IOPS | Weighted mean latency (us) |',
           '|---|---|---:|---:|---:|---:|---:|']
    for r in runs:
        lines.append(f'| {r["label"]} | {r["family"]} | {r["bs_kib"]} | {r["total_qd"]} | {r["jobs"]} | {r["sum_job_iops"]:.1f} | {r["lat_weighted_mean_us"]:.2f} |')
    lines += ['', '## Interpretation', '',
        '- Sum job IOPS uses each job\'s measurement window; it is not an exact common-wall-clock aggregate.',
        '- Mean latencies are weighted by completed requests. Per-job percentiles are in per_job.csv; they are not pooled percentiles.',
        '- Requested QD and job count do not identify hardware NVMe queues or NAND occupancy.',
        '- Arrival tests use finite timestamped replay, with a finite outstanding cap. Inspect arrival_audit.csv before comparing burst/smooth.',
        '- Schedule error is relative to the first logged submission, not absolute launch delay or device issue delay. Negative values are retained.',
        '- fio clat logs may omit final drain requests. Missing completions are counted, never synthesized; JSON+ covers whole-run completion statistics.',
        '- Arrival traces include startup and drain, no warmup. Configured rate is an intended offered rate, not measured throughput.',
        '- Neither matching means nor matching external curves proves the internal mechanism. Preserve fast and slow observations.',
        '- Arrival workloads are held out by default. Declare any additional training/validation split before fitting.']
    (stage/'report.md').write_text('\n'.join(lines)+'\n')
    stage.rename(output)
    print(f'Analysis: {output}')


def preflight(a, plan):
    a.file=a.file.resolve(strict=True)
    st=a.file.stat()
    if not a.file.is_file() or st.st_blocks*512 < st.st_size:
        raise ValueError('Need an existing populated, non-sparse regular input file')
    if st.st_size < max(c['size_mib'] for c in plan)*1024**2:
        raise ValueError('Input smaller than largest working set')
    for path in (a.file,a.ram_root):
        if any(c in str(path) for c in '\n\r\t :;#\\'):
            raise ValueError('Unsupported fio trace/path characters')
    backing=Path(f'/sys/dev/block/{os.major(st.st_dev)}:{os.minor(st.st_dev)}').resolve()
    if a.device not in backing.parts: raise ValueError('Input is not directly on selected device')
    fio=shutil.which('fio')
    if not fio: raise ValueError('fio not installed')
    version=capture([fio,'--version'])
    match=re.match(r' fio-(\d+)\.(\d+)', ' '+version['output'])
    if not match: raise ValueError('Not the fio I/O benchmark')
    if any(c['family']=='arrival' for c in plan) and tuple(map(int,match.groups())) < (3,36):
        raise ValueError('Arrival issue-time logging requires fio >= 3.36')
    if capture(['findmnt','-n','-o','FSTYPE','-T',str(a.ram_root)])['output']!='tmpfs':
        raise ValueError('Output must use tmpfs; no disk fallback')
    # Conservative budget: raw per-request logs, iolog, JSON histograms, plus headroom.
    need=1024**3+sum(c.get('requests',0)*600 for c in plan)
    mem=dict(l.split(':',1) for l in Path('/proc/meminfo').read_text().splitlines())
    if shutil.disk_usage(a.ram_root).free<need or int(mem['MemAvailable'].split()[0])*1024<need+1024**3:
        raise ValueError(f'Need {need/1024**3:.1f} GiB tmpfs and an extra 1 GiB available RAM')
    return fio,version,backing


def main():
    a=parser().parse_args()
    if a.analyze:
        if a.run: raise ValueError('--analyze and --run are mutually exclusive')
        analyze(a.analyze.resolve());return
    validate(a);plan=make_plan(a)
    nominal=sum(a.arrival_seconds if c['family']=='arrival' else a.runtime+a.ramp for c in plan)
    print(json.dumps(dict(runs=len(plan), nominal_seconds=nominal,
                         note='Estimate excludes fio setup, final drain and archival/analysis; arrival timing is a target.',
                         plan=plan),indent=2))
    if not a.run:
        print('PLAN ONLY. No fio process or input-file I/O was started. Add --run to execute.');return
    fio,version,backing=preflight(a,plan)
    out=Path(tempfile.mkdtemp(prefix='mqsim_suite_',dir=a.ram_root.resolve()))
    print(f'Tmpfs: {out}',flush=True)
    controller=next((part for part in reversed(backing.parts) if re.fullmatch(r'nvme\d+',part)),None)
    controller_path=Path('/sys/class/nvme')/(controller or 'unavailable')
    metadata=dict(suite='calibration-v1',plan=plan,
                  device_model=text_file(controller_path/'model'),
                  device_firmware=text_file(controller_path/'firmware_rev'),
                  hmb_enabled=text_file(controller_path/'hmb'),
                  arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(a).items()},
                  fio=version,backing=str(backing),input_size=a.file.stat().st_size,
                  input_mtime_ns=a.file.stat().st_mtime_ns,
                  uname=capture(['uname','-a']),
                  lsblk=capture(['lsblk','-J','-o','NAME,MODEL,SERIAL,REV,SIZE,TRAN,MOUNTPOINTS']),
                  scheduler=text_file(f'/sys/class/block/{a.device}/queue/scheduler'),
                  allowed_cpus=sorted(os.sched_getaffinity(0)),
                  git=capture(['git','-C',str(REPO),'rev-parse','HEAD']),
                  git_status=capture(['git','-C',str(REPO),'status','--short']))
    (out/'metadata.json').write_text(json.dumps(metadata,indent=2))
    for source in (Path(__file__),REPO/'microbench/read_sweep.py',REPO/'microbench/analyze_read_sweep.py',REPO/'ssdconfig.xml'):
        if source.exists(): shutil.copyfile(source,out/(source.name+'.snapshot'))
    stop=threading.Event()
    observer=threading.Thread(target=monitor,args=(stop,out/'system_samples.jsonl',a.device))
    observer.start(); archived=None
    try:
        with (out/'events.jsonl').open('w',buffering=1) as events:
            for index,case in enumerate(plan):
                run=out/case['label'];run.mkdir()
                if case['family']=='arrival':
                    digest=arrival_trace(run/'requests.iolog',a.file,case,a.burst)
                    (run/'address_sequence.sha256').write_text(digest+'\n')
                (run/'job.fio').write_text(job_text(a,case,run))
                command=[fio,'--readonly','--output-format=json+',f'--output={run/"fio.json"}',str(run/'job.fio')]
                (run/'command.json').write_text(json.dumps(command))
                print(f'{index+1}/{len(plan)} {case["label"]}',flush=True)
                def event(name, **extra):
                    events.write(json.dumps(dict(event=name,run=case['label'],wall_time_ns=time.time_ns(),monotonic_ns=time.monotonic_ns(),**extra))+'\n')
                event('start')
                with (run/'stdout.txt').open('w') as stdout,(run/'stderr.txt').open('w') as stderr:
                    process=subprocess.Popen(command,stdout=stdout,stderr=stderr)
                    try: code=process.wait(timeout=a.timeout)
                    except BaseException:
                        process.terminate()
                        try: process.wait(timeout=10)
                        except subprocess.TimeoutExpired: process.kill();process.wait()
                        raise
                event('end',returncode=code)
                if code: raise RuntimeError(f'fio failed in {run}')
                extract(json.loads((run/'fio.json').read_text()),case)
        (out/'COMPLETE').write_text(f'{len(plan)} successful fio runs\n')
    except BaseException as exc:
        (out/'INCOMPLETE').write_text(f'{type(exc).__name__}: {exc}\n');raise
    finally:
        stop.set();observer.join()
        original_error=sys.exc_info()[0] is not None
        try:
            archived=archive_results(out,a.archive_root.resolve())
            print(f'Archive: {archived}',flush=True)
        except Exception as exc:
            print(f'ARCHIVE FAILED: {exc}; tmpfs retained at {out}',file=sys.stderr)
            if not original_error: raise
    analyze(archived)
    if a.family == 'pipeline':
        subprocess.run([sys.executable, '-B', str(REPO/'microbench/analyze_pipeline.py'), str(archived)], check=True)
    if a.paired_arrivals:
        subprocess.run([sys.executable, "-B", str(REPO/"microbench/analyze_burst_sweep.py"), str(archived)], check=True)


if __name__=='__main__':
    main()
