#!/usr/bin/env python3
"""Analyze intended vs achieved load from raw fio archives; never starts fio."""
import argparse
import csv
import json
from pathlib import Path
import statistics
import tempfile
from analyze_read_sweep import verify_archive, write_csv
from calibration_suite import extract


def arrival_metrics(run, case, burst):
    paths=list(run.glob('latency_slat.*.log'))
    if len(paths)!=1: raise ValueError('Expected a single submission log')
    times=[]; offsets=[]
    with paths[0].open() as f:
        for row in csv.reader(f):
            if len(row)!=7 or int(row[2])!=0: raise ValueError('Unsupported fio log')
            times.append(int(row[6])); offsets.append(int(row[4]))
    planned=[]
    for line in (run/'requests.iolog').read_text().splitlines():
        fields=line.split()
        if len(fields)==5 and fields[2]=='read': planned.append((int(fields[0]),int(fields[3])))
    if offsets!=[p[1] for p in planned] or len(times)!=case['requests']:
        raise ValueError('Submitted addresses/count do not match intended trace')
    gaps=[(b-a)/1000 for a,b in zip(times,times[1:])]
    if not gaps or min(gaps)<0 or times[-1]<=times[0]: raise ValueError('Invalid issue-time sequence')
    span=(times[-1]-times[0])/1e9
    achieved=(len(times)-1)/span
    within=[g for i,g in enumerate(gaps,1) if i%burst]
    boundary=[g for i,g in enumerate(gaps,1) if not i%burst]
    return dict(submission_rate_iops=achieved,
                submission_rate_over_target=achieved/case['rate'],
                issue_span_s=span,
                final_relative_schedule_error_ms=(span*1e6-(planned[-1][0]-planned[0][0]))/1000,
                gap_median_us=statistics.median(gaps),
                burst_within_median_us=statistics.median(within) if case['mode']=='burst' and within else None,
                burst_boundary_median_us=statistics.median(boundary) if case['mode']=='burst' and boundary else None), offsets


def compare(smooth, burst, tolerance):
    mismatch=abs(burst['submission_rate_iops']/smooth['submission_rate_iops']-1)
    target_error=max(abs(smooth['submission_rate_over_target']-1),abs(burst['submission_rate_over_target']-1))
    return dict(repeat=smooth['repeat'], target_iops=smooth['target_iops'],
                smooth_submission_iops=smooth['submission_rate_iops'],
                burst_submission_iops=burst['submission_rate_iops'],
                actual_rate_mismatch_fraction=mismatch,
                smooth_lat_mean_us=smooth['lat_mean_us'], burst_lat_mean_us=burst['lat_mean_us'],
                burst_over_smooth_mean_latency=burst['lat_mean_us']/smooth['lat_mean_us'],
                smooth_lat_p99_us=smooth['lat_p99_us'], burst_lat_p99_us=burst['lat_p99_us'],
                burst_over_smooth_p99=burst['lat_p99_us']/smooth['lat_p99_us'],
                review_flag='rate_mismatch' if mismatch>tolerance else ('target_not_reached' if target_error>tolerance else 'rate_checks_only_passed'))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('archive',type=Path)
    p.add_argument('--out',type=Path)
    p.add_argument('--rate-tolerance',type=float,default=.02,
                   help='Descriptive review threshold only; not proof of equivalence')
    a=p.parse_args()
    if not 0<a.rate_tolerance<1: raise ValueError('Tolerance must be between 0 and 1')
    root=a.archive.resolve();manifest=verify_archive(root)
    meta=json.loads((root/'metadata.json').read_text())
    out=a.out or root/'burst_analysis'
    if out.exists(): raise FileExistsError(f'Refusing to overwrite {out}')
    burst=meta['arguments']['burst'];rows=[];pairs={};addresses={}
    for case in meta['plan']:
        if case['family']!='arrival':continue
        run=root/case['label']
        for file in [run/'fio.json',run/'requests.iolog',*run.glob('latency_slat.*.log')]:
            if str(file.relative_to(root)) not in manifest['sha256']:raise ValueError('Unverified input')
        base=extract(json.loads((run/'fio.json').read_text()),case)[0]
        metric,offsets=arrival_metrics(run,case,burst)
        row=dict(run=case['label'],repeat=case['repeat'],mode=case['mode'],target_iops=case['rate'],
                 requests=case['requests'],burst_size=burst,qd_cap=case['total_qd'],
                 fio_completed_iops=base['iops'],lat_mean_us=base['lat_mean_us'],
                 lat_p99_us=base['lat_p99_us'],clat_mean_us=base['clat_mean_us'],
                 slat_mean_us=base['slat_mean_us'], **metric)
        rows.append(row)
        key=(case['repeat'],case['rate'])
        if key in addresses and addresses[key]!=offsets:raise ValueError('Pair address sequences differ')
        addresses[key]=offsets
        if case['mode'] in pairs.setdefault(key,{}):raise ValueError('Duplicate pair member')
        pairs[key][case['mode']]=row
    if not rows:raise ValueError('No arrival cases found')
    comparisons=[]
    for key,pair in sorted(pairs.items(),key=lambda x:(x[0][1],x[0][0])):
        if set(pair)!={'smooth','burst'}:raise ValueError(f'Incomplete pair {key}')
        comparisons.append(compare(pair['smooth'],pair['burst'],a.rate_tolerance))
    out.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.burst-analysis-',dir=out.parent))
    write_csv(stage/'per_run.csv',rows);write_csv(stage/'paired.csv',comparisons)
    lines=['# Burst / smooth load sweep','',
           'Raw archive checksums, request counts and paired ordered addresses verified.',
           f'Rate review threshold: {a.rate_tolerance:.1%}. This is a diagnostic threshold, not statistical equivalence.',
           'Submission rate = (N-1)/(last-first fio issue time). Completion IOPS comes separately from fio JSON.',
           '', '| Target IOPS | Repeat | Smooth issue IOPS | Burst issue IOPS | Burst/smooth mean lat | Burst/smooth p99 | Review |',
           '|---:|---:|---:|---:|---:|---:|---|']
    for r in comparisons:
        lines.append(f'| {r["target_iops"]} | {r["repeat"]} | {r["smooth_submission_iops"]:.1f} | {r["burst_submission_iops"]:.1f} | {r["burst_over_smooth_mean_latency"]:.3f} | {r["burst_over_smooth_p99"]:.3f} | {r["review_flag"]} |')
    lines += ['', '## Limits', '',
        '- Keep all pairs, including mismatches. No rate correction, latency correction or sample deletion.',
        '- A passed rate check does not establish the same SSD state, precise arrival shape, or causal equivalence.',
        '- This fio replay retains known pacing drift. Inspect achieved load, gap shape and schedule error before interpreting latency.',
        '- Queue caps and the host generator can throttle arrival. Falling behind does not identify SSD saturation or an internal bottleneck.',
        '- Relative schedule error includes generator drift; it is not SSD latency. Latency statistics use complete JSON, not incomplete drain logs.',
        '- Sequential execution and alternating pair order reduce some ordering bias but do not eliminate device-state confounding.',
        '- High target rates are exploratory, not claimed achievable device loads. No MQSim fit performed.']
    (stage/'report.md').write_text('\n'.join(lines)+'\n')
    stage.rename(out);print(f'Burst analysis: {out}')


if __name__=='__main__':main()
