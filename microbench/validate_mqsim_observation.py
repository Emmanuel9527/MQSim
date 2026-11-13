#!/usr/bin/env python3
"""User-invoked simulator validation; not a fitted calibration experiment."""
import json, shutil, subprocess, uuid
from pathlib import Path
import xml.etree.ElementTree as ET
from mqsim_calibrate import ROOT, sha, source_hashes, read_result, record_calibration

def main():
    old=ROOT/'microbench/results/mqsim_calibration_initial_pilot'
    for rel,h in json.loads((old/'result_manifest.json').read_text()).items():
        if sha(old/rel)!=h:raise ValueError('Historical pilot checksum mismatch: '+rel)
    out=ROOT/'microbench/results'/('mqsim_observation_check_'+uuid.uuid4().hex[:8]);out.mkdir()
    shutil.copy2(ROOT/'MQSim',out/'MQSim.binary');binary=out/'MQSim.binary'
    shutil.copy2(Path(__file__),out/'validation.py.snapshot')
    meta=dict(source_hashes=source_hashes(),binary_sha256=sha(binary),cases=[])
    specs=[('legacy_qd1',1,None,'RANDOM_UNIFORM',4096),('legacy_qd32',32,None,'RANDOM_UNIFORM',4096)]
    specs += [(f'{mode}_{mib}MiB',32,mib*2048,mode,4096) for mib in (256,32768) for mode in ('RANDOM_UNIFORM','STREAMING')]
    summaries=[]
    record_calibration(out,['## 觀測驗證：'+out.name,'','狀態：已開始；非正式校準。'])
    try:
        for name,qd,sectors,mode,count in specs:
            run=out/name;run.mkdir()
            src=old/f'reference_s321_qd{qd}'
            shutil.copy2(src/'ssdconfig.xml',run/'ssdconfig.xml')
            tree=ET.parse(src/'workload.xml');flow=tree.find('.//IO_Flow_Parameter_Set_Synthetic')
            flow.find('Address_Distribution').text=mode
            if sectors is not None:ET.SubElement(flow,'Working_Set_Sectors').text=str(sectors)
            tree.write(run/'workload.xml')
            case=dict(label=name,variant=mode,seed=321,qd=qd,requests=count,intended_role='observation_validation',sectors=sectors)
            meta['cases'].append(case)
            (out/'execution.json').write_text(json.dumps(meta,indent=2))
            print('Running',name,'in',out,flush=True)
            with (run/'stdout.txt').open('w') as stdout,(run/'stderr.txt').open('w') as stderr:
                subprocess.run([str(binary),'-i',str(run/'ssdconfig.xml'),'-w',str(run/'workload.xml')],cwd=run,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,check=True,timeout=3600)
            result=read_result(run/'workload_scenario_1.xml',case)
            xml=ET.parse(run/'workload_scenario_1.xml')
            obs={e.tag:int(e.text) for e in xml.iter() if e.tag.startswith('Observed_Read_')}
            if obs.get('Observed_Read_Completed')!=count or obs.get('Observed_Read_Open_Mapping_At_Completion')!=0:raise ValueError('Observation completion invariant failed')
            if not 0 <= obs['Observed_Read_PHY_Data_Out_Departures'] <= count:raise ValueError('Unexpected PHY data-out queue population')
            if obs['Observed_Read_Mapping_Blocked_Count']>count or obs['Observed_Read_TSU_Departures']!=count:raise ValueError('Unexpected transaction population; inspect raw output')
            if sectors is None:
                before=read_result(src/'workload_scenario_1.xml',case)
                for key in ['iops','device_mean_us','end_to_end_mean_us','transaction_turnaround_us','transaction_waiting_us','transaction_execution_us','transaction_transfer_us']:
                    if result[key]!=before[key]:raise ValueError('Legacy regression: '+name+' '+key)
            summaries.append(dict(result,**obs))
        (out/'observations.json').write_text(json.dumps(summaries,indent=2))
        (out/'COMPLETE').write_text('Validation passed; not calibrated.\n')
        lines=['## 觀測驗證：'+out.name,'','狀態：六組完成，legacy數值回歸與計數檢查通過；不是正式校準。','',f'原始輸出：`{out}`。','','| Case | Completed reads | Mapping mean ns / all reads | TSU mean ns / all reads | PHY data-out queue mean ns / all reads |','|---|---:|---:|---:|---:|']
        for s in summaries:
            n=s['Observed_Read_Completed'];lines.append(f"| {s['label']} | {n} | {s['Observed_Read_Mapping_Wait_Sum_NS']/n:.3f} | {s['Observed_Read_TSU_Queue_Sum_NS']/n:.3f} | {s['Observed_Read_PHY_Data_Out_Queue_Sum_NS']/n:.3f} |")
        lines+=['','僅4096筆功能驗證：不保證工作範圍已走完／cache已暖機，不以此表推論容量或正式趨勢；尚無逐筆LBA邊界稽核。']
        record_calibration(out,lines)
    except Exception as e:
        (out/'INCOMPLETE').write_text(repr(e));record_calibration(out,['## 觀測驗證：'+out.name,'',f'狀態：INCOMPLETE，{e}；保留原始輸出於 `{out}`。']);raise
    finally:
        manifest={str(p.relative_to(out)):sha(p) for p in out.rglob('*') if p.is_file() and p.name!='result_manifest.json'}
        (out/'result_manifest.json').write_text(json.dumps(manifest,indent=2))
    print('Finished:',out)
if __name__=='__main__':main()
