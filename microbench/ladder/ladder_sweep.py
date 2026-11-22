#!/usr/bin/env python3
"""Run a list of ladder_sim jobs in parallel.
Usage: ladder/ladder_sweep.py <jobs.json> <out.jsonl> [--procs 12]
jobs.json = [{"name": ..., "params": {ladder_sim parameters incl. sched, profile}}, ...]; each output line =
{"name", "params", "result"}. Refuses to overwrite out.jsonl.
"""
import argparse, json, os, sys, time
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ladder_sim


def job(j):
    t0 = time.time()
    r = ladder_sim.run(j['params'])
    return {'name': j['name'], 'params': j['params'], 'result': r, 'wall_s': round(time.time() - t0, 1)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('jobs'); ap.add_argument('out'); ap.add_argument('--procs', type=int, default=12)
    a = ap.parse_args()
    if os.path.exists(a.out): raise SystemExit(f'Refusing to overwrite {a.out}')
    jobs = json.load(open(a.jobs))
    with Pool(min(a.procs, 12)) as p, open(a.out, 'w') as f:
        for i, o in enumerate(p.imap(job, jobs)):
            f.write(json.dumps(o) + '\n'); f.flush()
            print(f"[{i + 1}/{len(jobs)}] {o['name']} qps {o['result']['qps']:.0f} ({o['wall_s']}s)", flush=True)


if __name__ == '__main__':
    main()
