#!/usr/bin/env python3
"""Timing of co-located expansion: replay graphsim schedules (--dump_sched) through ladder_sim (host mode).

Each graphsim run gives, per query, the reads of every round (sector = slot // 10, so unit = (sector // 4) % 96 and
the flash page is sector // 4) and the number of nodes expanded in that round. Co-located nodes come with the page
that was read: no extra tR. Not modelled: channel transfer of the extra sectors (~1.7 us per 4 KiB, vs tR 70 us).
The host compute gap between rounds is scaled by expanded nodes / reads of the run (DiskANN's gap was measured with
one node per read).
Usage: ladder/ladder_coloc.py <dir with *.pkl + *.json> <out.jsonl> [--loads T8_W4 T16_W4] [--repeat 4]
"""
import argparse, json, pickle, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ladder_sim

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument('dir', type=Path)
ap.add_argument('out', type=Path)
ap.add_argument('--loads', nargs='+', default=['T8_W4', 'T16_W4'])
ap.add_argument('--profiles', type=Path, default=Path(__file__).resolve().parents[1] / 'configs/host_profiles')
ap.add_argument('--repeat', type=int, default=4)
ap.add_argument('--page_key', action='store_true', help='reads of one flash page share a key: in-flight merge + page buffer')
ap.add_argument('--buf', type=int, default=0, help='>0: SSD keeps this many sensed pages (buf_on_read, LRU)')
ap.add_argument('--match', default='', help='only schedules whose name contains this')
ap.add_argument('--host_cache', type=int, default=0, help='>0: host DRAM cache of the N most-read sectors (of --host_cache_from, else of the replayed schedule: in-sample, optimistic)')
ap.add_argument('--host_cache_from', type=Path, default=None, help='schedule (other queries) that ranks the hot sectors; cached reads are removed before --page_key')
a = ap.parse_args()
hot = None
if a.host_cache and a.host_cache_from:
    src = pickle.load(open(a.host_cache_from, 'rb'))
    hot = {x for x, _ in Counter(x for q in src['queries'] for r in q for x in r['F']).most_common(a.host_cache)}
with open(a.out, 'w') as fo:
    for pk in sorted(a.dir.glob('*.pkl')):
        if a.match and a.match not in pk.stem: continue
        js = pk.with_suffix('.json')
        if not js.exists(): continue
        meta = json.load(open(js)); s = pickle.load(open(pk, 'rb'))
        reads = sum(len(r['F']) for q in s['queries'] for r in q)
        exp = sum(r['exp'] for q in s['queries'] for r in q)
        gs = exp / reads  # host work per read is unchanged by caching: computed before cached reads are removed
        cached = 0
        if hot is not None:
            s = dict(s, queries=[[dict(r, F=[x for x in r['F'] if x not in hot]) for r in q] for q in s['queries']])
            cached = (reads - sum(len(r['F']) for q in s['queries'] for r in q)) / len(s['queries'])
        if a.page_key:
            s = dict(s, queries=[[dict(r, F=[x // 4 * 4 for x in r['F']]) for r in q] for q in s['queries']])
        for load in a.loads:
            T = int(load.split('_')[0][1:])
            extra = {'buf_on_read': 1, 'pf_buffer': a.buf} if a.buf else {}
            if a.host_cache and hot is None: extra['host_cache'] = a.host_cache
            res = ladder_sim.run(dict({'workers': T, 'repeat': a.repeat, 'gap_scale': gs, 'seed': 1}, **extra),
                                 sched=s, profile=ladder_sim.load_profile(a.profiles / f'{load}.txt'))
            row = dict(run=pk.stem, load=load, page_key=a.page_key, buf=a.buf, host_cache=a.host_cache, host_cache_from=str(a.host_cache_from or ''), host_cached_oos=cached, layout=meta['layout'], coexp=meta['coexp'],
                       filter=meta.get('filter_spec', ''), L=meta['L'], recall=meta['mean']['recall'],
                       expanded=meta['mean']['expanded'], gap_scale=gs, **res)
            fo.write(json.dumps(row) + '\n'); fo.flush()
            print(f"{pk.stem:32s} {load:7s} gap x{gs:.2f} QPS {res['qps']:7.0f} lat {res['latency_mean_us']:6.0f}us "
                  f"recall {meta['mean']['recall']:.4f} unit_util {res['unit_util']:.2f}", flush=True)
