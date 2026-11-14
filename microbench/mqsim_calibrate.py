#!/usr/bin/env python3
"""Prepare/run/analyze MQSim diagnostic calibration cases. No fio is launched."""
import argparse
import copy
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from analyze_read_sweep import verify_archive, write_csv
from read_sweep import summarize

ROOT=Path(__file__).resolve().parent.parent
DEFAULT_REFERENCE=ROOT/'microbench/results/mqsim_long_20261112T153235Z_c53192bf'


def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def source_hashes():
    return {str(p.relative_to(ROOT)):sha(p) for p in sorted((ROOT/'src').rglob('*')) if p.suffix in ('.cpp','.h')} | {'Makefile':sha(ROOT/'Makefile')}


def setval(tree,name,value):
    e=tree.find('.//'+name)
    if e is None:raise ValueError(f'Missing XML setting {name}')
    e.text=str(value)


def geometry(tree):
    def integer(name):return int(tree.findtext('.//'+name))
    planes=math.prod(integer(n) for n in ['Flash_Channel_Count','Chip_No_Per_Channel','Die_No_Per_Chip','Plane_No_Per_Die'])
    page=integer('Page_Capacity')
    if page%512:raise ValueError('Expected 512-byte sector multiple')
    perplane=int(integer('Block_No_Per_Plane')*integer('Page_No_Per_Block')*(page//512)*(1-float(tree.findtext('.//Overprovisioning_Ratio'))))
    sectors=planes*perplane
    target=32*1024**3
    percent=max(1,min(100,round(target/(sectors*512)*100)))
    # Same integer-per-plane allocation and inclusive endpoint construction as source.
    end=int((sectors-1)*(percent/100))
    return dict(logical_capacity_bytes=sectors*512,working_set_percentage=percent,
                generated_endpoint_lba=end,approx_working_set_bytes=(end+1)*512,
                real_working_set_bytes=target)


def workload(config,qd,seed,count,working_percent,clean=False):
    root=ET.Element('MQSim_IO_Scenarios');scenario=ET.SubElement(root,'IO_Scenario')
    flow=ET.SubElement(scenario,'IO_Flow_Parameter_Set_Synthetic')
    values={'Priority_Class':'HIGH','Device_Level_Data_Caching_Mode':'WRITE_CACHE',
            'Initial_Occupancy_Percentage':75,'Working_Set_Percentage':working_percent,
            'Synthetic_Generator_Type':'QUEUE_DEPTH','Read_Percentage':100,
            'Address_Distribution':'RANDOM_UNIFORM','Percentage_of_Hot_Region':0,
            'Generated_Aligned_Addresses':'true','Address_Alignment_Unit':8,
            'Request_Size_Distribution':'FIXED','Average_Request_Size':8,'Variance_Request_Size':0,
            'Seed':seed,'Average_No_of_Reqs_in_Queue':qd,'Bandwidth':0,
            'Stop_Time':0,'Total_Requests_To_Generate':count}
    for tag,field in [('Channel_IDs','Flash_Channel_Count'),('Chip_IDs','Chip_No_Per_Channel'),
                      ('Die_IDs','Die_No_Per_Chip'),('Plane_IDs','Plane_No_Per_Die')]:
        values[tag]=','.join(map(str,range(int(config.findtext('.//'+field)))))
    if clean:
        # Exact 32GiB span (512-byte sectors) and every page written during preconditioning.
        values['Working_Set_Sectors']=32*1024*1024*2;values['Precondition_Full_Working_Set']='true'
    for k,v in values.items():ET.SubElement(flow,k).text=str(v)
    return ET.ElementTree(root)


def export_reference(paths,out):
    """Merge read_sweep archives; for a (repeat, QD) present in several, the LATER path wins."""
    merged={};sources=[]
    for path in paths:
        verify_archive(path)
        meta=json.loads((path/'metadata.json').read_text())
        a=meta['arguments']
        if a.get('block_kib')!=4 or a.get('size_gib')!=32:raise ValueError('Reference must be 4KiB/32GiB read_sweep archive')
        for case in meta['schedule']:
            label=f'r{case["repeat"]}_qd{case["qd"]}'
            row=summarize(json.loads((path/label/'fio.json').read_text()),label,case['qd'],case['repeat'])
            row['fio_jobs']=a.get('jobs',1)
            row['source']=str(path/label/'fio.json');row['source_sha256']=sha(path/label/'fio.json')
            merged[(case['repeat'],case['qd'])]=row
        sources.append({'path':str(path),'manifest_sha256':sha(path/'archive_manifest.json'),'fio_jobs':a.get('jobs',1)})
    rows=[merged[k] for k in sorted(merged)]
    write_csv(out/'real_per_run.csv',rows)
    return {'sources':sources,
            'note':'All repeats retained; 60-second measured windows after read warmup. When archives overlap, the later --reference wins per (repeat, QD); repeats from different archives are different runs.'}


def record_calibration(root, lines):
    """One canonical Markdown log; replace only this run's generated section."""
    import fcntl
    document=ROOT/'microbench/mqsim_calibration.md'
    token=hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:16]
    begin=f'<!-- calibration-run:{token}:begin -->'
    end=f'<!-- calibration-run:{token}:end -->'
    block=begin+'\n'+ '\n'.join(lines)+'\n'+end
    document.touch(exist_ok=True)
    with document.open('r+',encoding='utf-8') as f:
        fcntl.flock(f,fcntl.LOCK_EX)
        f.seek(0);text=f.read()
        if begin in text:
            first=text.index(begin);last=text.index(end,first)+len(end)
            text=text[:first]+block+text[last:]
        else:text=text.rstrip()+'\n\n'+block+'\n'
        f.seek(0);f.write(text);f.truncate();f.flush()
        fcntl.flock(f,fcntl.LOCK_UN)
    return document


def trend_rows(rows, real):
    """Normalize each model seed and each real repeat to its own QD1."""
    simulated={(r['variant'],r['seed'],r['qd']):r for r in rows}
    measured={(int(r['repeat']),int(r['requested_qd'])):r for r in real}
    result=[]
    for r in rows:
        qd=r['qd'];base=simulated.get((r['variant'],r['seed'],1))
        if base is None:raise ValueError('Trend comparison requires QD1 for every variant/seed')
        if base['iops']<=0 or base['end_to_end_mean_us']<=0:raise ValueError('Invalid QD1 normalization denominator')
        qds=sorted(x['qd'] for x in rows if x['variant']==r['variant'] and x['seed']==r['seed'])
        prev=qds[qds.index(qd)-1] if qd!=qds[0] else None
        gains=[];growth=[];increments=[]
        for repeat in sorted({int(x['repeat']) for x in real}):
            a=measured.get((repeat,1));b=measured.get((repeat,qd))
            if a is None or b is None:continue
            gains.append(float(b['iops'])/float(a['iops']))
            growth.append(float(b['lat_mean_us'])/float(a['lat_mean_us']))
            prior=measured.get((repeat,prev))
            if prior:increments.append(float(b['iops'])/float(prior['iops']))
        if not gains:raise ValueError('No real repeat with both QD1 and requested QD')
        simgain=r['iops']/base['iops'];simgrowth=r['end_to_end_mean_us']/base['end_to_end_mean_us']
        row=dict(label=r['label'],variant=r['variant'],seed=r['seed'],qd=qd,
                 sim_iops_gain_from_qd1=simgain,sim_latency_growth_from_qd1=simgrowth,
                 real_iops_gain_median=statistics.median(gains),real_iops_gain_min=min(gains),real_iops_gain_max=max(gains),
                 real_latency_growth_median=statistics.median(growth),real_latency_growth_min=min(growth),real_latency_growth_max=max(growth),
                 previous_compared_qd=prev,sim_iops_step_gain=None,real_iops_step_gain_median=None,
                 sim_log_iops_slope=None,real_log_iops_slope_median=None)
        if prev is not None:
            prior=simulated[(r['variant'],r['seed'],prev)]
            step=r['iops']/prior['iops']
            row.update(sim_iops_step_gain=step,real_iops_step_gain_median=statistics.median(increments) if increments else None,
                       sim_log_iops_slope=math.log(step)/math.log(qd/prev),
                       real_log_iops_slope_median=statistics.median(math.log(x)/math.log(qd/prev) for x in increments) if increments else None)
        result.append(row)
    return result


def prepare(a):
    if 1 not in a.qds:raise ValueError('Trend calibration requires QD1 in --qds')
    if a.requests<max(a.qds) or min(a.qds)<1 or len(set(a.qds))!=len(a.qds):raise ValueError('Need unique positive QDs and requests >= max QD')
    if not a.seeds or len(set(a.seeds))!=len(a.seeds):raise ValueError('Seeds must be unique')
    base=ET.parse(ROOT/'ssdconfig.xml')
    if base.findtext('.//HostInterface_Type')!='NVME':raise ValueError('NVMe config required')
    if int(base.findtext('.//IO_Queue_Depth'))<=max(a.qds):raise ValueError('Queue capacity must exceed generated QD')
    out=(a.out or ROOT/'microbench/results'/('mqsim_calibration_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:8])).resolve()
    out.mkdir(parents=True,exist_ok=False)
    shutil.copyfile(ROOT/'ssdconfig.xml',out/'input_ssdconfig.xml')
    shutil.copyfile(Path(__file__),out/'mqsim_calibrate.py.snapshot')
    ref=export_reference([p.resolve() for p in a.reference],out)
    variants={'reference':{}}
    if a.sensitivity:
        for name,factor in [('tr_minus20',.8),('tr_plus20',1.2)]:
            variants[name]={tag:round(int(base.findtext('.//'+tag))*factor) for tag in ['Page_Read_Latency_LSB','Page_Read_Latency_CSB','Page_Read_Latency_MSB']}
        # SSD_Device.cpp quantizes the channel timing as floor(1000/rate)*2 ns.
        # Choose neighboring representable timing bins; a nominal -20% may be a no-op.
        rate=int(base.findtext('.//Channel_Transfer_Rate'))
        half_period=1000//rate
        if half_period<2:raise ValueError('Channel sensitivity needs a baseline half-period >=2 ns')
        for name,ticks in [('channel_slower',half_period+1),('channel_faster',half_period-1)]:
            candidate=1000//ticks
            if 1000//candidate!=ticks:raise ValueError('Unrepresentable channel timing bin')
            variants[name]={'Channel_Transfer_Rate':candidate}
        for name,factor in [('cmt_half',.5),('cmt_double',2)]:
            variants[name]={'CMT_Capacity':round(int(base.findtext('.//CMT_Capacity'))*factor)}
    for spec in a.variant:
        name,pairs=spec[0],spec[1:]
        if name in variants or not pairs or any('=' not in x for x in pairs):raise ValueError(f'Bad --variant {spec}')
        variants[name]={k:v for k,v in (x.split('=',1) for x in pairs)}
    if any('=' not in x for x in a.base_override):raise ValueError('--base-override needs KEY=VALUE')
    base_override={k:v for k,v in (x.split('=',1) for x in a.base_override)}
    for k in [*base_override,*(k for c in variants.values() for k in c)]:
        if base.find('.//'+k) is None:raise ValueError(f'Unknown ssdconfig tag {k}')
    cases=[]
    for variant,changes in variants.items():
        for seed in a.seeds:
            for qd in a.qds:
                label=f'{variant}_s{seed}_qd{qd}';run=out/label;run.mkdir()
                config=copy.deepcopy(base)
                setval(config,'Enabled_Preconditioning','true')
                setval(config,'Seed',seed)
                for k,v in {**base_override,**changes}.items():setval(config,k,v)
                if a.clean:ET.SubElement(config.find('.//Device_Parameter_Set'),'GC_Enabled').text='false'
                geo=geometry(config)
                config.write(run/'ssdconfig.xml',encoding='us-ascii',xml_declaration=True)
                workload(config,qd,seed,a.requests,geo['working_set_percentage'],a.clean).write(run/'workload.xml',encoding='us-ascii',xml_declaration=True)
                cases.append(dict(label=label,variant=variant,seed=seed,qd=qd,requests=a.requests,
                                  intended_role='fit_candidate' if qd in [1,4,16,64] else 'reserved_comparison',
                                  overrides={**base_override,**changes},**geo))
    manifest=dict(kind='mqsim-diagnostic-v1',cases=cases,source_hashes=source_hashes(),reference=ref,
                  status='prepared_not_executed',model_claim='Uncalibrated candidate, not a hardware reconstruction of the reference SSD',
                  common_overrides={'Enabled_Preconditioning':True,'Initial_Occupancy_Percentage':75,'clean':bool(a.clean),**base_override},
                  sensitivity_note='One-parameter-group perturbations are diagnostic choices, NOT measured hardware ranges or calibrated estimates.',
                  files={str(p.relative_to(out)):sha(p) for p in out.rglob('*') if p.is_file()})
    (out/'plan.json').write_text(json.dumps(manifest,indent=2))
    record_calibration(out,[f'## 執行紀錄：{out.name}', '', f'狀態：已準備 {len(cases)} 個cases，尚未執行。', f'輸入與來源：`{out}`。', '目標：QD吞吐增益、延遲成長及曲線轉折；絕對數值僅供追溯。'])
    print(f'Prepared {len(cases)} cases; no simulator executed.\n{out}')
    return out


def read_result(path,case):
    root=ET.parse(path).getroot();flows=list(root.iter('Host.IO_Flow'))
    if len(flows)!=1:raise ValueError('Expected exactly one Host.IO_Flow')
    f=flows[0]
    n=int(f.findtext('Request_Count'))
    if n!=case['requests'] or int(f.findtext('Read_Request_Count'))!=n or int(f.findtext('Write_Request_Count'))!=0:
        raise ValueError('Unexpected generated request counts')
    # XML Request_Count counts generated work; transferred read bytes verify completed work.
    if float(f.findtext('Bytes_Transferred_Read'))!=n*4096 or float(f.findtext('Bytes_Transferred_Write'))!=0:
        raise ValueError('Not all planned reads completed, or unexpected writes')
    row={k:case[k] for k in ['label','variant','seed','qd','requests','intended_role']}
    row.update(iops=float(f.findtext('IOPS')),device_mean_us=float(f.findtext('Device_Response_Time')),
               end_to_end_mean_us=float(f.findtext('End_to_End_Request_Delay')))
    if row['iops']<=0:raise ValueError('Invalid IOPS')
    for phase in ('Turnaround','Execution','Transfer','Waiting'):
        nodes=list(root.iter(f'Average_Read_Transaction_{phase}_Time'))
        if len(nodes)!=1:raise ValueError(f'Expected one transaction {phase} metric')
        row[f'transaction_{phase.lower()}_us']=float(nodes[0].text)
    return row


def check_inputs(root,plan):
    for rel,digest in plan['files'].items():
        p=root/rel
        if not p.resolve().is_relative_to(root):raise ValueError('Invalid plan path')
        if sha(p)!=digest:raise ValueError(f'Input changed: {rel}')


def run(a):
    root=a.directory.resolve();plan=json.loads((root/'plan.json').read_text());check_inputs(root,plan)
    if source_hashes()!=plan['source_hashes']:raise ValueError('Source changed since preparation; prepare a new directory')
    if (root/'execution.json').exists():raise FileExistsError('Run already attempted; preserve it and prepare a new directory')
    if not (ROOT/'MQSim').is_file():raise FileNotFoundError('Build MQSim first using the bash launcher')
    shutil.copyfile(ROOT/'MQSim',root/'MQSim.binary');binary=root/'MQSim.binary';binary.chmod(0o755)
    (root/'execution.json').write_text(json.dumps(dict(binary_sha256=sha(binary),start_epoch_ns=time.time_ns(),
                                                       source_hashes=source_hashes()),indent=2))
    record_calibration(root,[f'## 執行紀錄：{root.name}', '', '狀態：模擬已啟動，尚未完成。', f'共 {len(plan["cases"])} 個cases；原始資料：`{root}`。'])
    try:
        for i,case in enumerate(plan['cases']):
            folder=root/case['label'];command=[str(binary),'-i',str(folder/'ssdconfig.xml'),'-w',str(folder/'workload.xml')]
            (folder/'command.json').write_text(json.dumps(command))
            print(f'{i+1}/{len(plan["cases"])} {case["label"]}',flush=True)
            with (folder/'stdout.txt').open('w') as stdout,(folder/'stderr.txt').open('w') as stderr:
                process=subprocess.Popen(command,cwd=folder,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr)
                try:code=process.wait(timeout=a.timeout)
                except BaseException:
                    process.terminate()
                    try:process.wait(timeout=10)
                    except subprocess.TimeoutExpired:process.kill();process.wait()
                    raise
            if code:raise RuntimeError(f'MQSim exited {code} in {folder}')
            read_result(folder/'workload_scenario_1.xml',case)
        results={str(p.relative_to(root)):sha(p) for p in root.rglob('*') if p.is_file()}
        (root/'result_manifest.json').write_text(json.dumps(results,indent=2))
        (root/'COMPLETE').write_text('All cases completed and checked\n')
    except BaseException as e:
        (root/'INCOMPLETE').write_text(f'{type(e).__name__}: {e}\n')
        record_calibration(root,[f'## 執行紀錄：{root.name}', '', f'狀態：INCOMPLETE；{type(e).__name__}: {e}', f'原始資料：`{root}`。'])
        raise
    analyze(root)


def analyze(root):
    root=root.resolve()
    if not (root/'COMPLETE').exists() or (root/'INCOMPLETE').exists():raise ValueError('Incomplete simulation run')
    for rel,digest in json.loads((root/'result_manifest.json').read_text()).items():
        p=root/rel
        if not p.resolve().is_relative_to(root) or sha(p)!=digest:raise ValueError(f'Changed output: {rel}')
    plan=json.loads((root/'plan.json').read_text());check_inputs(root,plan)
    out=root/'analysis'
    out.mkdir(exist_ok=True)
    rows=[read_result(root/c['label']/'workload_scenario_1.xml',c) for c in plan['cases']]
    write_csv(out/'sim_per_run.csv',rows)
    with (root/'real_per_run.csv').open() as f:real=list(csv.DictReader(f))
    comparisons=[]
    for row in rows:
        rr=[r for r in real if int(r['requested_qd'])==row['qd']]
        if not rr:raise ValueError('Missing reference QD')
        r_iops=[float(r['iops']) for r in rr];r_lat=[float(r['lat_mean_us']) for r in rr]
        comparisons.append(dict(row,real_iops_median=statistics.median(r_iops),real_iops_min=min(r_iops),real_iops_max=max(r_iops),
                                real_lat_mean_us_median=statistics.median(r_lat),
                                iops_ratio_to_real_median=row['iops']/statistics.median(r_iops),
                                latency_ratio_different_boundaries=row['end_to_end_mean_us']/statistics.median(r_lat)))
    write_csv(out/'comparison.csv',comparisons)
    trends=trend_rows(rows,real)
    write_csv(out/'trend_per_run.csv',trends)
    lines=[f'## 執行紀錄：{root.name}', '',
           '狀態：完整執行、原始輸出校驗通過；尚未宣稱校準完成。',
           f'原始資料與CSV：`{root}`。', '',
           '### 趨勢比較（主要目標）', '',
           '模擬每個seed、真機每個repeat各自以QD1正規化。真機欄位為repeat內比值的中位數及min/max，不是信賴區間。',
           '同repeat的不同QD在不同時間執行，因此快慢狀態可能影響正規化結果；比值不能消除這個混淆。', '',
           '| Variant | Seed | QD | 模擬吞吐增益 | 真機吞吐增益中位數 [min,max] | 模擬延遲成長 | 真機延遲成長中位數 [min,max] |',
           '|---|---:|---:|---:|---|---:|---|']
    for t in trends:
        lines.append(f'| {t["variant"]} | {t["seed"]} | {t["qd"]} | {t["sim_iops_gain_from_qd1"]:.3f} | {t["real_iops_gain_median"]:.3f} [{t["real_iops_gain_min"]:.3f}, {t["real_iops_gain_max"]:.3f}] | {t["sim_latency_growth_from_qd1"]:.3f} | {t["real_latency_growth_median"]:.3f} [{t["real_latency_growth_min"]:.3f}, {t["real_latency_growth_max"]:.3f}] |')
    lines += ['', '完整相鄰測試QD增益與log(IOPS)對log(QD)斜率見 `analysis/trend_per_run.csv`。',
              '兩點短測不能識別飽和轉折；即使多點也不自動把外部平台當作NAND飽和。',
              '', '### 絕對數值（背景與追溯，不作主要擬合目標）', '', 'Status: UNCALIBRATED. No best parameter set selected.',
           'All measured repeats and simulation seeds retained. Ratios are descriptive, not fit acceptance.', '',
           '| Variant | Seed | QD | Sim IOPS | Real median [min,max] | Sim device us | Sim end-to-end us | Real fio mean us (median) |',
           '|---|---:|---:|---:|---|---:|---:|---:|']
    for r in comparisons:
        lines.append(f'| {r["variant"]} | {r["seed"]} | {r["qd"]} | {r["iops"]:.1f} | {r["real_iops_median"]:.1f} [{r["real_iops_min"]:.1f}, {r["real_iops_max"]:.1f}] | {r["device_mean_us"]:.1f} | {r["end_to_end_mean_us"]:.1f} | {r["real_lat_mean_us_median"]:.1f} |')
    geo=plan['cases'][0]
    lines+=['','### 本輪限制', '',
            f'- Simulator working set approx {geo["approx_working_set_bytes"]/1024**3:.4f} GiB; real working set 32 GiB. Integer-percent interface prevents exact alignment.',
            '- Sim timing XML is integer microseconds (source implementation), not nanoseconds as README states. Sub-us precision is already lost.',
            '- Sim counts include startup/drain, with no measured-phase read warmup. Real baseline has a read warmup and a 60-second measured phase.',
            '- Sim preconditioning at 75% occupancy is an initialization assumption, not measured GM7 occupancy or a reproduced history.',
            '- Topology, technology, cache and timing values inherited from local XML are candidate assumptions; they are not verified GM7 specifications.',
            '- fio and simulator latency boundaries differ. Host submission/scheduling cost must not be folded into NAND read latency.',
            '- Transaction execution/transfer/waiting averages are not an additive decomposition of host-request latency: multiple transactions can overlap.',
            '- No p99 is invented; this output provides only existing aggregate mean/min/max statistics.',
            '- Reserved QDs were declared after seeing real data; they are comparison points, not blind independent validation.',
            '- Sensitivity changes are parameter experiments only. Burst behavior and hardware mechanisms are not validated.']
    from plot_mqsim_calibration import plot_trends
    figures=plot_trends(rows,real,out/'figures')
    if figures:
        import os
        lines += ['', '### 趨勢圖', '', '細線為各次量測，粗線為逐點中位數，陰影為min/max而非信賴區間。每個seed／repeat分別正規化；不同QD在不同時間執行，連線不代表固定SSD狀態下的因果曲線。']
        for variant,base,path in figures:
            link=Path(os.path.relpath(path,ROOT/'microbench')).as_posix()
            lines += ['', f'![{variant}：以QD{base}正規化的吞吐與延遲]({link})', '', f'以QD{base}為基準；左圖為吞吐增益、右圖為平均延遲成長。'+('這張圖避開QD1的變動，但仍保留QD64的快速量測。' if base==32 else 'QD1本身的快慢差異會影響整條正規化曲線。')]
    document=record_calibration(root,lines)
    print(f'Calibration record: {document}')


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='mode',required=True)
    prep=sub.add_parser('prepare');prep.add_argument('--out',type=Path);prep.add_argument('--reference',type=Path,nargs='+',default=[DEFAULT_REFERENCE],help='read_sweep archives; later ones override overlapping (repeat, QD)')
    prep.add_argument('--variant',nargs='+',action='append',default=[],metavar='NAME KEY=VALUE',help='extra variant: name then XML tag=value pairs (repeatable)')
    prep.add_argument('--base-override',nargs='+',default=[],metavar='KEY=VALUE',help='XML tag=value applied to every variant, including reference')
    prep.add_argument('--qds',type=int,nargs='+',default=[1,2,4,8,16,32,64,128]);prep.add_argument('--seeds',type=int,nargs='+',default=[321,322,323])
    prep.add_argument('--requests',type=int,default=100000);prep.add_argument('--sensitivity',action='store_true')
    prep.add_argument('--clean',action='store_true',help='GC_Enabled=false, exact 32GiB working set, full working-set preconditioning')
    execute=sub.add_parser('run');execute.add_argument('directory',type=Path);execute.add_argument('--timeout',type=int,default=3600)
    report=sub.add_parser('analyze');report.add_argument('directory',type=Path)
    a=p.parse_args()
    if a.mode=='prepare':prepare(a)
    elif a.mode=='run':run(a)
    else:analyze(a.directory)


if __name__=='__main__':main()
