#!/usr/bin/env python3
"""Run explicit user-requested window probes, or re-analyze existing outputs."""
import argparse,csv,json,shutil,subprocess,uuid,os
from pathlib import Path
import xml.etree.ElementTree as ET
from mqsim_calibrate import ROOT,sha,source_hashes,read_result,record_calibration

def analyze(root):
    for rel,h in json.loads((root/'manifest.json').read_text()).items():
        if sha(root/rel)!=h:raise ValueError('Changed archive: '+rel)
    if not (root/'COMPLETE').exists():raise ValueError('Incomplete run')
    plan=json.loads((root/'plan.json').read_text());derived=[]
    for case in plan['cases']:
        rows=list(csv.DictReader((root/case['label']/'workload.IO_Flow.No_0.log').open(),delimiter='\t'))
        rows=[{k:int(v) for k,v in row.items()} for row in rows]
        rows=[row for i,row in enumerate(rows) if i==0 or row['completed']!=rows[i-1]['completed']]  # exit snapshot can trail the final completion by a few ns with no new completions
        if rows[0]['completed']!=0 or rows[-1]['completed']!=case['requests']:raise ValueError('Missing checkpoint endpoints')
        for a,b in zip(rows,rows[1:]):
            dt=b['time_ns']-a['time_ns'];n=b['completed']-a['completed']
            if dt<=0 or n<=0:raise ValueError('Invalid checkpoint interval')
            for key in ['generated','latency_sum_ns','device_sum_ns','read_bytes','cmt_queries_global','cmt_misses_global','mapping_reads_global']:
                if b[key]<a[key]:raise ValueError('Non-monotonic counter: '+key)
            query=b['cmt_queries_global']-a['cmt_queries_global'];miss=b['cmt_misses_global']-a['cmt_misses_global']
            derived.append(dict(case=case['label'],begin_ns=a['time_ns'],end_ns=b['time_ns'],completed=n,iops=n*1e9/dt,latency_us=(b['latency_sum_ns']-a['latency_sum_ns'])/n/1000,queries=query,misses=miss,mapping_reads=b['mapping_reads_global']-a['mapping_reads_global'],phase='startup' if b['time_ns']<=plan['warmup_ns'] else 'boundary' if a['time_ns']<plan['warmup_ns'] else 'drain' if b['generated']==case['requests'] else 'measurement'))
    out=root/'analysis';out.mkdir(exist_ok=True)
    with (out/'windows.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(derived[0]));w.writeheader();w.writerows(derived)
    os.environ.setdefault('MPLCONFIGDIR','/tmp/mqsim-calibration-mpl')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(3,1,figsize=(10,9),sharex=True)
    lines=['## 分段checkpoint實驗：'+root.name,'',f'狀態：執行完成；warmup切點 {plan["warmup_ns"]/1e9:g} 模擬秒為預先選定診斷設定，不保證已穩態。','','| Case | 後段完成數 | 後段IOPS | 後段mean latency µs |','|---|---:|---:|---:|']
    for case in plan['cases']:
        rr=[x for x in derived if x['case']==case['label']];valid=[x for x in rr if x['phase']=='measurement'];n=sum(x['completed'] for x in valid);dt=sum(x['end_ns']-x['begin_ns'] for x in valid)
        lines.append(f"| {case['label']} | {n} | {n*1e9/dt if dt else 'N/A'} | {sum(x['latency_us']*x['completed'] for x in valid)/n if n else 'N/A'} |")
        for ax,key in zip(axes,['iops','latency_us','mapping_reads']):ax.plot([x['end_ns']/1e9 for x in rr],[x[key] for x in rr],label=case['label'],lw=.8)
    for ax,label in zip(axes,['Window IOPS','Completion-window mean latency (us)','Mapping reads per checkpoint interval']):ax.set_ylabel(label);ax.axvline(plan['warmup_ns']/1e9,color='black',ls='--');ax.grid(alpha=.2)
    axes[0].legend(fontsize=8);axes[-1].set_xlabel('Simulation time (s)');fig.tight_layout()
    for ext in ['png','svg']:fig.savefig(out/f'windows.{ext}',dpi=160)
    plt.close(fig)
    link=os.path.relpath(out/'windows.png',ROOT/'microbench')
    lines+=['',f'![分段趨勢]({link})','','原始log時間為模擬時間。每段在完成事件越過period後記錄，非精確等寬窗口；最後段較短，mapping讀取數不可直接視作固定時間速率。latency以該窗口完成的requests為母體，包含跨窗口到達的工作。CMT／mapping為全裝置累計差值，本實驗僅單flow，不與完成數硬配對。startup、跨warmup邊界及最後generated達上限的區間不納入後段彙總；仍應檢查後段是否持續漂移。功能短測不代表穩態。']
    record_calibration(root,lines);print('Analyzed:',root)

def main():
    p=argparse.ArgumentParser();p.add_argument('--smoke',action='store_true');p.add_argument('--analyze',type=Path);p.add_argument('--profile',choices=['reference','four_channel'],default='reference');p.add_argument('--requests',type=int);p.add_argument('--no-gc',action='store_true',help='Write GC_Enabled=false into the device config');p.add_argument('--full-ws',action='store_true',help='Write Precondition_Full_Working_Set=true into the workload');p.add_argument('--case',choices=['RANDOM_UNIFORM_256MiB','RANDOM_UNIFORM_32768MiB','STREAMING_256MiB','STREAMING_32768MiB']);a=p.parse_args()
    if a.analyze:return analyze(a.analyze.resolve())
    count=a.requests if a.requests is not None else (65536 if a.smoke else 16777216)
    if count < 32:raise ValueError("Need at least QD32 requests")
    root=ROOT/'microbench/results'/('mqsim_windows_'+uuid.uuid4().hex[:8]);root.mkdir()
    shutil.copy2(ROOT/'MQSim',root/'MQSim.binary');shutil.copy2(__file__,root/'script.snapshot')
    plan=dict(warmup_ns=1000000000,requests=count,source_hashes=source_hashes(),cases=[],smoke=a.smoke,profile=a.profile,gc_enabled=not a.no_gc,precondition_full_working_set=a.full_ws,topology_note="four_channel keeps total chips/dies/capacity constant via 4 channels x 8 chips; not verified GM7 geometry")
    src=ROOT/'microbench/results/mqsim_calibration_initial_pilot/reference_s321_qd32'
    for mode in ['RANDOM_UNIFORM','STREAMING']:
        for mib in [256,32768]:
            label=f'{mode}_{mib}MiB'
            if a.case and label!=a.case:continue
            d=root/label;d.mkdir();c=dict(label=label,variant=mode,seed=321,qd=32,requests=count,intended_role='window_probe');plan['cases'].append(c)
            config=ET.parse(src/'ssdconfig.xml');config.find('.//Enable_ResponseTime_Logging').text='true';config.find('.//ResponseTime_Logging_Period_Length').text='10000000';
            if a.profile=='four_channel':
                config.find('.//Flash_Channel_Count').text='4';config.find('.//Chip_No_Per_Channel').text='8'
            if a.no_gc:ET.SubElement(config.find('.//Device_Parameter_Set'),'GC_Enabled').text='false'
            config.write(d/'ssdconfig.xml')
            work=ET.parse(src/'workload.xml');f=work.find('.//IO_Flow_Parameter_Set_Synthetic');f.find('Total_Requests_To_Generate').text=str(count);f.find('Address_Distribution').text=mode
            if a.profile=='four_channel':
                f.find('Channel_IDs').text='0,1,2,3';f.find('Chip_IDs').text='0,1,2,3,4,5,6,7'
            ET.SubElement(f,'Working_Set_Sectors').text=str(mib*2048)
            if a.full_ws:ET.SubElement(f,'Precondition_Full_Working_Set').text='true'
            work.write(d/'workload.xml')
    (root/'plan.json').write_text(json.dumps(plan,indent=2));record_calibration(root,['## 分段checkpoint實驗：'+root.name,'','狀態：執行中。'])
    try:
        for c in plan['cases']:
            d=root/c['label'];print('Running',c['label'],root,flush=True)
            with (d/'stdout.txt').open('w') as out,(d/'stderr.txt').open('w') as err:subprocess.run([str(root/'MQSim.binary'),'-i',str(d/'ssdconfig.xml'),'-w',str(d/'workload.xml')],cwd=d,stdin=subprocess.DEVNULL,stdout=out,stderr=err,check=True,timeout=14400)
            read_result(d/'workload_scenario_1.xml',c)
        (root/'COMPLETE').write_text('All four cases completed\n')
    except Exception as e:
        (root/'INCOMPLETE').write_text(repr(e));record_calibration(root,['## 分段checkpoint實驗：'+root.name,'',f'狀態：INCOMPLETE；{e}']);raise
    finally:
        (root/'manifest.json').write_text(json.dumps({str(f.relative_to(root)):sha(f) for f in root.rglob('*') if f.is_file() and f.name!='manifest.json'},indent=2))
    analyze(root)
if __name__=='__main__':main()
