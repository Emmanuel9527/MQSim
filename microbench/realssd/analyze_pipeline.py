#!/usr/bin/env python3
"""Summarize batch/load sweep and continuous-QD controls; existing files only."""
import argparse
import csv
import json
from pathlib import Path
import statistics as stats
import tempfile
from analyze_read_sweep import verify_archive, write_csv
from calibration_suite import extract


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('archive',type=Path)
    a=p.parse_args();root=a.archive.resolve();manifest=verify_archive(root)
    meta=json.loads((root/'metadata.json').read_text())
    if meta['arguments']['family']!='pipeline': raise ValueError('Expected pipeline suite')
    out=root/'pipeline_analysis'
    if out.exists(): raise FileExistsError(out)
    stage=Path(tempfile.mkdtemp(prefix='.pipeline-',dir=root));rows=[];windows=[]
    for case in meta['plan']:
        relative=f'{case["label"]}/fio.json'
        if relative not in manifest['sha256']:raise ValueError('Unverified JSON')
        job=extract(json.loads((root/relative).read_text()),case)[0]
        row=dict(run=case['label'],repeat=case['repeat'],kind=case['family'],batch_size=case.get('batch_size'),
                 target_iops=case.get('rate'),interval_us=case.get('batch_size',0)*1e6/case['rate'] if 'rate' in case else None,
                 qd_cap=case['total_qd'],completed_iops=job['iops'],mean_latency_us=job['lat_mean_us'],
                 per_run_p99_us=job['lat_p99_us'],submission_iops=None,rate_error_fraction=None,
                 batch_start_interval_median_us=None,batch_width_median_us=None,batch_width_p99_us=None,
                 final_schedule_lag_ms=None,timing_review='not_applicable_closed_loop')
        if case['family']=='arrival':
            paths=list((root/case['label']).glob('latency_slat.*.log'))
            if len(paths)!=1 or str(paths[0].relative_to(root)) not in manifest['sha256']:
                raise ValueError('Expected verified slat log')
            times=[];offsets=[]
            with paths[0].open() as f:
                for r in csv.reader(f):
                    if len(r)!=7 or int(r[2])!=0:raise ValueError('Unsupported log format')
                    times.append(int(r[6]));offsets.append(int(r[4]))
            trace=root/case['label']/'requests.iolog'
            if str(trace.relative_to(root)) not in manifest['sha256']:raise ValueError('Unverified trace')
            planned=[l.split() for l in trace.read_text().splitlines() if ' read ' in l]
            if len(times)!=case['requests'] or offsets!=[int(l[3]) for l in planned]:raise ValueError('Submission mismatch')
            if any(y<x for x,y in zip(times,times[1:])):raise ValueError('Nonmonotonic timestamps')
            span=(times[-1]-times[0])/1e9
            if span<=0:raise ValueError('Invalid span')
            rate=(len(times)-1)/span;error=rate/case['rate']-1;b=case['batch_size']
            starts=times[::b]
            intervals=[(y-x)/1000 for x,y in zip(starts,starts[1:])]
            widths=sorted((times[i+b-1]-times[i])/1000 for i in range(0,len(times),b))
            row.update(submission_iops=rate,rate_error_fraction=error,
                       batch_start_interval_median_us=stats.median(intervals) if intervals else None,
                       batch_width_median_us=stats.median(widths),batch_width_p99_us=widths[min(len(widths)-1,int(.99*len(widths)))],
                       final_schedule_lag_ms=(span*1e6-(int(planned[-1][0])-int(planned[0][0])))/1000,
                       timing_review='rate_error_gt_2pct' if abs(error)>.02 else 'rate_only_within_2pct')
            bins={}
            for t in times: bins[(t-times[0])//1000000000]=bins.get((t-times[0])//1000000000,0)+1
            for sec,n in sorted(bins.items()):
                windows.append(dict(run=case['label'],second=sec,submitted_requests=n,
                                    full_second=bool(sec+1<=span)))
        rows.append(row)
    write_csv(stage/'per_run.csv',rows);write_csv(stage/'submission_windows.csv',windows)
    lines=['# Batch × load exploration','',
           'Checksums verified. All repeats retained. No SSD internal utilization inferred.',
           'Batch = requests with the same intended timestamp, NOT one io_submit call.',
           'Timing flags are diagnostics, not a causal-validity certificate. Inspect batch widths and all repeats.','',
           '| Kind | B / QD | Target IOPS | Median completed IOPS | Median mean latency us | Median run p99 us | Timing flags |',
           '|---|---:|---:|---:|---:|---:|---|']
    groups={}
    for r in rows:
        key=(r['kind'],r['batch_size'] if r['kind']=='arrival' else r['qd_cap'],r['target_iops'] or 0)
        groups.setdefault(key,[]).append(r)
    for (kind,b,rate),rr in sorted(groups.items()):
        vals=[stats.median(r[k] for r in rr) for k in ['completed_iops','mean_latency_us','per_run_p99_us']]
        flags=sum(r['timing_review']=='rate_error_gt_2pct' for r in rr)
        lines.append(f'| {kind} | {b} | {rate or "unpaced"} | {vals[0]:.1f} | {vals[1]:.1f} | {vals[2]:.1f} | {flags}/{len(rr)} rate errors |')
    lines += ['', '## Interpretation limits','',
              '- Timestamp replay is not yet established to meet the schedule. A timing-mismatched point cannot establish a sweet spot at its target load.',
              '- Continuous controls use a read warmup; finite batch tests include startup/drain. They are reference envelopes, not precisely matched phase comparisons.',
              '- The highest continuous throughput is an observed reference, not proven NAND saturation.',
              '- Per-run p99 medians are not pooled p99. Each run is only one sample of changing device/host state.',
              '- Schedule lag is relative to first fio submission, not absolute application-generation latency; no end-to-end demand latency claim is made.',
              '- This phase does not collect block/NVMe driver traces. Their boundaries must be measured separately for selected conditions before internal attribution.']
    (stage/'report.md').write_text('\n'.join(lines)+'\n');stage.rename(out)
    print(f'Pipeline analysis: {out}')


if __name__=='__main__':main()
