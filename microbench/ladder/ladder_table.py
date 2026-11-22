#!/usr/bin/env python3
"""Pipeline-ladder study: aggregate a sweep (.jsonl from ladder/ladder_sweep.py) over seeds.
Usage: ladder/ladder_table.py <sweep.jsonl> [--base 'L0 base'] [--keys qps,latency_mean_us,...]
Names are 'tag|config|variant|seed' (variant may contain '|'); prints mean over seeds, min-max of QPS, and QPS /
latency change relative to the base variant of the same config. Also importable: load(path) -> {(config, variant): row}.
"""
import argparse, collections, json


def load(path):
    acc = collections.defaultdict(list)
    for l in open(path):
        o = json.loads(l); parts = o['name'].split('|')
        acc[(parts[1], '|'.join(parts[2:-1]))].append(o['result'])
    out = {}
    for k, rs in acc.items():
        row = {m: sum(r[m] for r in rs) / len(rs) for m in rs[0]}
        row['qps_min'] = min(r['qps'] for r in rs); row['qps_max'] = max(r['qps'] for r in rs); row['n'] = len(rs)
        out[k] = row
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('sweep'); ap.add_argument('--base', default='L0 base')
    ap.add_argument('--keys', default='round_mean_us,read_wait_mean_us,hit_ratio,attach_ratio,nand_reads_per_query,unit_util,dsp_util,buf_chan_util')
    ap.add_argument('--grep', default='')
    a = ap.parse_args()
    d = load(a.sweep); keys = a.keys.split(',')
    print('config | variant | qps (min-max) | dQPS | lat_us | dLat | p99_us | ' + ' | '.join(keys))
    for (c, v), r in d.items():
        if a.grep and a.grep not in v: continue
        b = d.get((c, a.base))
        dq = f"{100 * (r['qps'] / b['qps'] - 1):+.1f}%" if b else ''
        dl = f"{100 * (r['latency_mean_us'] / b['latency_mean_us'] - 1):+.1f}%" if b else ''
        spread = '' if r['qps_max'] / r['qps_min'] < 1.02 else f" ({r['qps_min']:.0f}-{r['qps_max']:.0f})"
        print(f"{c} | {v} | {r['qps']:.0f}{spread} | {dq} | {r['latency_mean_us']:.0f} | {dl} | {r['latency_p99_us']:.0f} | "
              + ' | '.join(f'{r[k]:.3g}' for k in keys))


if __name__ == '__main__':
    main()
