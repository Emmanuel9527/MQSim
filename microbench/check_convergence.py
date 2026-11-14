#!/usr/bin/env python3
"""Descriptive convergence checks from full one-second fio IOPS windows."""
import argparse
import csv
import json
from pathlib import Path
import statistics as st
from analyze_read_sweep import verify_archive, write_csv


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run',type=Path)
    args=parser.parse_args();root=args.run
    verify_archive(root)
    output=root/'analysis'
    output.mkdir(exist_ok=True)
    targets=[output/name for name in ('convergence.csv','iops_windows.csv','convergence.md')]
    if any(p.exists() for p in targets):
        raise SystemExit('Convergence outputs already exist; refusing overwrite')
    metadata=json.loads((root/'metadata.json').read_text());summaries=[];windows=[]
    for item in metadata['schedule']:
        label=f'r{item["repeat"]}_qd{item["qd"]}'
        logs=sorted((root/label).glob('iops_iops.*.log'))
        if not logs:raise ValueError(f'{label}: no fio IOPS log')
        # Multi-job runs write one log per job; sum them window by window on the first job's clock.
        per_job=[[(int(r[0]),float(r[1])) for r in csv.reader(p.open()) if int(r[2])==0] for p in logs]
        n=min(len(d) for d in per_job)
        data=[(per_job[0][i][0],sum(d[i][1] for d in per_job)) for i in range(n)]
        # The first sample has no previous timestamp; final sample may be partial.
        # Reject irregular windows instead of treating them as full seconds.
        retained=[(t,v) for (prev,_),(t,v) in zip(data[:-2],data[1:-1]) if 950<=t-prev<=1050]
        values=[v for t,v in retained]
        if len(values)<2:raise ValueError(f'{label}: insufficient full-window samples')
        accum=0
        for i,(timestamp,value) in enumerate(retained,1):
            accum+=value
            windows.append(dict(run=label,requested_qd=item['qd'],epoch_ms=timestamp,
                                retained_window_index=i,iops=value,cumulative_mean_window_iops=accum/i))
        half=len(values)//2;first=st.mean(values[:half]);second=st.mean(values[half:]);mean=st.mean(values)
        row=dict(run=label,requested_qd=item['qd'],retained_windows=len(values),
                 omitted_windows=len(data)-len(values),mean_window_iops=mean,
                 first_half_mean_iops=first,second_half_mean_iops=second,
                 second_over_first=second/first if first else None,
                 min_window_iops=min(values),max_window_iops=max(values))
        for duration in (30,60,120,300,600):
            prefix=st.mean(values[:duration]) if len(values)>=duration else None
            row[f'first_{duration}_windows_mean_iops']=prefix
            row[f'first_{duration}_vs_final_relative_diff']=abs(prefix-mean)/mean if prefix is not None and mean else None
        summaries.append(row)
    write_csv(targets[0],summaries);write_csv(targets[1],windows)
    lines=['# Long-run stability review','',
           'These are descriptive checks, not statistical proof of stationarity.',
           'Full-ish one-second windows only: first/last/irregular windows omitted explicitly.',
           'Cumulative values are means of retained window IOPS, not exact whole-run completed-count/time.',
           'Use original fio JSON for exact whole-run throughput and request-weighted mean latency.',
           'Averaged latency logs cannot yield pooled p99. Preserve original JSON+ histograms.', '',
           '| Run | Windows | First-half IOPS | Second-half IOPS | Second / first |',
           '|---|---:|---:|---:|---:|']
    for r in summaries:
        ratio=r['second_over_first']
        lines.append(f'| {r["run"]} | {r["retained_windows"]} | {r["first_half_mean_iops"]:.1f} | {r["second_half_mean_iops"]:.1f} | {ratio:.3f} |' if ratio is not None else f'| {r["run"]} | {r["retained_windows"]} | 0 | {r["second_half_mean_iops"]:.1f} | undefined |')
    lines+=['','Do not delete fast/slow windows. A stable overall mean can coexist with temporal modes.',
            'Compare independent repeats and longer durations before choosing a calibration target.',
            'QD is requested fio depth. No hardware cause or MQSim parameter fit is inferred.','']
    targets[2].write_text('\n'.join(lines))
    print(f'Convergence outputs: {output}')


if __name__=='__main__':main()
