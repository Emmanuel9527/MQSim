#!/usr/bin/env python3
"""Validate address replay, map file offsets, and summarize cache hypotheses."""
import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import re
import statistics as st

from analyze_read_sweep import verify_archive, write_csv


def describe(values):
    v=sorted(values)
    return {'count':len(v),'mean_us':st.mean(v) if v else None,
            'p50_us':st.median(v) if v else None,
            'p99_us':v[min(len(v)-1,int(.99*len(v)))] if v else None}


def analyze(root):
    verify_archive(root)
    meta=json.loads((root/'metadata.json').read_text());plan=meta['plan'];qd=meta['arguments']['qd']
    output=root/'address_analysis';output.mkdir(exist_ok=False)
    rows={r['run']:r for r in csv.DictReader((root/'summary.csv').open())}
    before=(root/'extents_before.txt').read_text();after=(root/'extents_after.txt').read_text()
    extents=[]
    for line in before.splitlines():
        m=re.match(r'\s*\d+:\s*(\d+)\.\.\s*(\d+):\s*(\d+)\.\.\s*(\d+):',line)
        if m:
            if any(flag in line for flag in ('unwritten','delalloc','unknown')):raise ValueError('Unsupported extent state')
            extents.append(tuple(map(int,m.groups())))
    if before!=after or not extents:raise ValueError('File extent mapping changed or could not be parsed')
    starts=[x[0] for x in extents];partition_start=int(meta['partition_start_sector'])
    def lba(offset):
        sector=offset//512;i=bisect_right(starts,sector)-1
        if i<0 or sector+7>extents[i][1]:raise ValueError('4KiB read not covered by an extent')
        return partition_start+extents[i][2]+sector-extents[i][0]
    summaries=[];groups=[];windows=[];pairs=[];previous={};warnings=[]
    for item in plan:
        label=item['label'];run=root/label
        offsets=[int(line.split()[-2]) for line in (run/'requests.iolog').read_text().splitlines() if ' read ' in line]
        sha=hashlib.sha256(','.join(map(str,offsets)).encode()).hexdigest()
        region_sha=hashlib.sha256(','.join(str(v-item['offset']) for v in offsets).encode()).hexdigest()
        key=(item['repeat'],item['size_mib'],item['condition'])
        if item['condition']!='logging' and item['phase']=='prime':previous[key]=set(offsets),sha,region_sha
        primed=previous.get(key,(set(),None,None))
        if item['phase']=='probe' and item['condition']!='logging':
            if item['condition']=='same' and sha!=primed[1]:raise ValueError('Same-sequence replay mismatch')
            if item['condition']=='region' and (region_sha!=primed[2] or set(offsets)&primed[0]):raise ValueError('Disjoint replay mismatch')
            prime_label=label.removesuffix('probe')+'prime'
            pairs.append(dict(repeat=item['repeat'],size_mib=item['size_mib'],condition=item['condition'],
                              prime_iops=float(rows[prime_label]['iops']),probe_iops=float(rows[label]['iops']),
                              probe_over_prime_iops=float(rows[label]['iops'])/float(rows[prime_label]['iops']),
                              prime_lat_us=float(rows[prime_label]['lat_mean_us']),probe_lat_us=float(rows[label]['lat_mean_us']),
                              overlap_with_prime_fraction=sum(o in primed[0] for o in offsets)/len(offsets)))
        if not item['detailed']:continue
        slat=list(csv.reader((run/'latency_slat.1.log').open()))
        if [int(r[4]) for r in slat]!=offsets:raise ValueError(f'{label}: actual submission order differs from planned addresses')
        request_ids={(int(r[6]),int(r[4])):i for i,r in enumerate(slat)}
        if len(request_ids)!=len(offsets):raise ValueError('Ambiguous issue-time/offset pair')
        seen=set();tags=[];last={};reuse_gaps=[]
        for i,o in enumerate(offsets):
            tags.append('repeat_in_run' if o in seen else 'first_in_run')
            if o in last:reuse_gaps.append(i-last[o])
            seen.add(o);last[o]=i
        clat=list(csv.reader((run/'latency_clat.1.log').open()));matched=set();values=defaultdict(list);bins=defaultdict(list)
        with (output/(label+'.requests.csv')).open('w') as f:
            w=csv.writer(f);w.writerow(['request_index','file_offset','derived_device_lba_512','fio_issue_ns','fio_completion_epoch_ms','clat_us','reuse_class','seen_in_pair_prime'])
            for r in clat:
                issue,offset=int(r[6]),int(r[4]);idx=request_ids[(issue,offset)]
                if idx in matched:raise ValueError('Duplicate completion log')
                matched.add(idx);lat=float(r[1])/1000;values[tags[idx]].append(lat)
                overlap=item['phase']=='probe' and offset in primed[0]
                values['in_pair_prime' if overlap else 'not_in_pair_prime'].append(lat)
                bins[issue//1000000000].append((lat,tags[idx]=='repeat_in_run',overlap))
                w.writerow([idx,offset,lba(offset),issue,int(r[0]),lat,tags[idx],int(overlap)])
        missing=len(offsets)-len(matched)
        if missing:warnings.append(f'{label}: {missing}/{len(offsets)} completion latency records missing; not fabricated. fio JSON reports {rows[label]["total_ios"]} completions.')
        for name,v in values.items():groups.append(dict(run=label,group=name,**describe(v)))
        for second,v in sorted(bins.items()):windows.append(dict(run=label,issue_second=second,recorded_requests=len(v),
                   clat_mean_us=st.mean(x[0] for x in v),repeated_in_run_fraction=st.mean(x[1] for x in v),
                   in_pair_prime_fraction=st.mean(x[2] for x in v)))
        summaries.append(dict(run=label,requests=len(offsets),unique_offsets=len(seen),
                         repeated_fraction=1-len(seen)/len(offsets),sequence_sha256=sha,
                         completion_records=len(matched),coverage=len(matched)/len(offsets),
                         median_repeat_request_gap=st.median(reuse_gaps) if reuse_gaps else None,
                         min_file_offset=min(offsets),max_file_offset=max(offsets)))
    write_csv(output/'address_summary.csv',summaries);write_csv(output/'paired_comparison.csv',pairs)
    write_csv(output/'reuse_latency.csv',groups);write_csv(output/'issue_windows.csv',windows)
    (output/'limitations.txt').write_text('\n'.join([
        'Derived device LBAs use unchanged filefrag 512-byte extents + partition start, not observed NVMe commands or NAND addresses.',
        'Prime is not cold; cache contents and firmware background work were not reset. Different random seeds still overlap.',
        'Equal request count, different run duration: not a fixed-rate burst experiment. QD64 and working sets do not identify a cache type.',
        'Request-gap is number of intervening submission positions, not distinct-page stack distance.',
        'Issue windows group by fio issue time; recorded_requests is not completion IOPS. Missing completions excluded explicitly.',
        'Within-run repeat latency associations may reflect time/queue effects; they do not prove hits.',
        'ABBA logging controls are in summary.csv. Retain all data; no simulated parameters changed.',*warnings])+'\n')
    print(output)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('run',type=Path);a=p.parse_args();analyze(a.run)
