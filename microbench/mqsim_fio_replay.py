#!/usr/bin/env python3
"""Replay fio_addr_replay.sh runs in MQSim (QD-limited, same addresses) and compare with the real block-layer data.

  prepare <sweep> --runs R...          parse each run's block.dat -> real_requests.csv, schedule.txt, trace.txt
  run     <sweep> --configs X.xml...   run MQSim for every (config, prepared run); outputs <sweep>/mqsim/<config>/<run>/
  compare <sweep>                      table of IOPS and hot/other latency, real vs every simulated config

QD-limited replay uses the closed-loop flow: QD virtual workers, request k goes to worker k % QD, each round is one
read. The per-request host turnaround of fio (QD*span/reads - mean block latency) is used as the gap between rounds.
This keeps QD requests in flight like fio does; slot assignment is round-robin instead of first-free (approximation).
"""
import argparse, json, os, re, shutil, subprocess, sys, xml.etree.ElementTree as ET
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_fio_addr_replay import block_reads  # noqa: E402
from diskann_trace_to_mqsim import load_layout  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def hot_sectors(sweep):
    src = Path([l.split('=', 1)[1] for l in (sweep / 'input.txt').read_text().splitlines() if l.startswith('source_trace=')][0])
    rr = np.loadtxt(sweep.parent / src.name / 'mqsim_replay/real_requests.csv', delimiter=',', skiprows=1, dtype=np.int64)
    u, c = np.unique(rr[:, 2] // 8, return_counts=True)
    return u[np.argsort(c)[::-1][:100]]


def prepare(sweep, runs):
    disk_dev, base, extents, _ = load_layout(sweep)
    starts = np.array([e[0] for e in extents]); ends = np.array([e[1] for e in extents]); fstart = np.array([e[2] for e in extents])
    for name in runs:
        run = sweep / name; qd = int(re.search(r'_qd(\d+)$', name).group(1))
        r = block_reads(run, disk_dev)
        lv = r[:, 2] - base
        i = np.searchsorted(starts, lv, side='right') - 1
        ok = (i >= 0) & (lv >= starts[np.maximum(i, 0)]) & (lv + r[:, 3] <= ends[np.maximum(i, 0)])
        r, lv, i = r[ok], lv[ok], i[ok]
        lba = fstart[i] + (lv - starts[i])
        span = (r[:, 0].max() - r[:, 0].min())
        turnaround = max(0, int(qd * span / len(r) - (r[:, 1] - r[:, 0]).mean()))
        np.savetxt(run / 'real_requests.csv', np.column_stack([r[:, 0], r[:, 1], lba, r[:, 3]]), fmt='%d', delimiter=',',
                   header='issue_ns,complete_ns,file_lba,sectors', comments='')
        with open(run / 'schedule.txt', 'w') as f:
            f.write(f'# QD-limited replay of {name}: {qd} workers, turnaround gap {turnaround} ns\n')
            for w in range(qd):
                for k in range(w, len(r), qd):
                    f.write(f'{w} {k} {0 if k < qd else turnaround} 0 1 0 {lba[k]} {r[k, 3]}\n')
        with open(run / 'trace.txt', 'w') as f:
            f.writelines(f'0 0 {a} {n} 1\n' for a, n in zip(lba, r[:, 3]))
        (run / 'replay_meta.json').write_text(json.dumps({'qd': qd, 'reads': int(len(r)), 'turnaround_ns': turnaround,
                                                          'dropped_outside_index': int((~ok).sum())}))
        print(f'{name}: {len(r)} reads, QD {qd}, turnaround {turnaround} ns')


def workload(cfg, trace, schedule, out):
    c = ET.parse(cfg)
    ids = lambda tag: ','.join(map(str, range(int(c.findtext('.//' + tag)))))
    root = ET.Element('MQSim_IO_Scenarios'); sc = ET.SubElement(root, 'IO_Scenario')
    f = ET.SubElement(sc, 'IO_Flow_Parameter_Set_Trace_Based')
    for k, v in [('Priority_Class', 'HIGH'), ('Device_Level_Data_Caching_Mode', 'WRITE_CACHE'),
                 ('Channel_IDs', ids('Flash_Channel_Count')), ('Chip_IDs', ids('Chip_No_Per_Channel')),
                 ('Die_IDs', ids('Die_No_Per_Chip')), ('Plane_IDs', ids('Plane_No_Per_Die')),
                 ('Initial_Occupancy_Percentage', 75), ('File_Path', trace), ('Percentage_To_Be_Executed', 100),
                 ('Relay_Count', 1), ('Time_Unit', 'NANOSECOND'), ('Replay_Schedule_File', schedule)]:
        ET.SubElement(f, k).text = str(v)
    ET.ElementTree(root).write(out, encoding='us-ascii', xml_declaration=True)


def run(sweep, configs):
    subprocess.run(['make', '-C', str(ROOT), '-j', os.environ.get('BUILD_JOBS', '4')], check=True)
    runs = sorted(p.name for p in sweep.iterdir() if (p / 'schedule.txt').is_file())
    for cfg in map(Path, configs):
        for name in runs:
            out = sweep / 'mqsim' / cfg.stem / name
            if out.exists(): print(f'skip existing {out}'); continue
            out.mkdir(parents=True)
            shutil.copy(cfg, out / 'ssdconfig.xml'); shutil.copy(ROOT / 'MQSim', out / 'MQSim.binary')
            workload(out / 'ssdconfig.xml', sweep / name / 'trace.txt', sweep / name / 'schedule.txt', out / 'workload.xml')
            env = dict(os.environ, MQSIM_REQUEST_LOG=str(out / 'mqsim_requests.txt'))
            print(f'=== {cfg.stem} {name}', flush=True)
            with open(out / 'stdout.txt', 'w') as so, open(out / 'stderr.txt', 'w') as se:
                p = subprocess.run([str(out / 'MQSim.binary'), '-i', 'ssdconfig.xml', '-w', 'workload.xml'], cwd=out,
                                   stdin=subprocess.DEVNULL, stdout=so, stderr=se)
            if p.returncode:
                (out / 'FAILED').write_text(str(p.returncode)); raise SystemExit(f'MQSim failed ({p.returncode}): {out}')
            print(''.join(l for l in open(out / 'stderr.txt') if 'FTL_EXIT' in l or 'HOST_EXIT' in l), end='')


def stats(lat, hot_mask):
    q = lambda x: [float(v) for v in np.percentile(x, [50, 99])] if len(x) else [None, None]
    return {'hot': q(lat[hot_mask]), 'other': q(lat[~hot_mask])}


def compare(sweep):
    hot = hot_sectors(sweep)
    rows = {}
    for run in sorted(p for p in sweep.iterdir() if (p / 'real_requests.csv').is_file()):
        r = np.loadtxt(run / 'real_requests.csv', delimiter=',', skiprows=1, dtype=np.int64)
        lo, hi = np.percentile(r[:, 0], [5, 95]); m = (r[:, 0] >= lo) & (r[:, 0] <= hi)
        lat = (r[:, 1] - r[:, 0]) / 1e3
        rows.setdefault(run.name, {})['real'] = {'iops': len(r) / ((r[:, 0].max() - r[:, 0].min()) / 1e9),
                                                 **stats(lat[m], np.isin(r[m, 2] // 8, hot))}
    for cfgdir in sorted((sweep / 'mqsim').iterdir()) if (sweep / 'mqsim').is_dir() else []:
        for out in sorted(cfgdir.iterdir()):
            log = out / 'mqsim_requests.txt'
            if not log.is_file() or out.name not in rows: continue
            s = np.loadtxt(log, skiprows=1, dtype=np.int64)
            lo, hi = np.percentile(s[:, 0], [5, 95]); m = (s[:, 0] >= lo) & (s[:, 0] <= hi)
            lat = (s[:, 2] - s[:, 0]) / 1e3
            rows[out.name][cfgdir.name] = {'iops': len(s) / ((s[:, 2].max() - s[:, 0].min()) / 1e9),
                                           **stats(lat[m], np.isin(s[m, 3] // 8, hot))}
    (sweep / 'mqsim_comparison.json').write_text(json.dumps(rows, indent=2))
    for name, d in rows.items():
        print(f'== {name}')
        for k, v in d.items():
            h, o = v['hot'], v['other']
            hs = f'hot p50/p99 {h[0]:6.0f}/{h[1]:6.0f}' if h[0] is not None else 'hot      -/     -'
            print(f'  {k:32s} iops {v["iops"]:8.0f} | {hs} | other p50/p99 {o[0]:6.0f}/{o[1]:6.0f}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('mode', choices=['prepare', 'run', 'compare'])
    ap.add_argument('sweep', type=Path)
    ap.add_argument('--runs', nargs='+', default=[])
    ap.add_argument('--configs', nargs='+', default=[])
    a = ap.parse_args()
    sweep = a.sweep.resolve()
    if a.mode == 'prepare': prepare(sweep, a.runs)
    elif a.mode == 'run': run(sweep, a.configs)
    else: compare(sweep)


if __name__ == '__main__':
    main()
