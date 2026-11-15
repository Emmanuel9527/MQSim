#!/usr/bin/env python3
"""Compare an MQSim closed-loop replay with the real DiskANN run it was built from.

Real side: closed_loop/real_rounds.csv (per round read_begin/read_end, first issue, last completion) and
mqsim_replay/real_requests.csv (per block request). Sim side: mqsim_rounds.txt / mqsim_requests.txt from the run.
Only the replayed rounds are compared (all of them, or the first N per worker in a smoke run).

Metrics: total span and read throughput, per-round time (read_begin -> read_end, i.e. what DiskANN waits for I/O),
per-query I/O-phase span (first read_begin -> last read_end), per-request device latency, in-flight over time.
Writes <run>/comparison.json and comparison.png.
"""
import json, sys
from pathlib import Path
import numpy as np


def st(x):
    x = np.asarray(x, dtype=float)
    return {'n': int(len(x)), 'mean': float(x.mean()), **{f'p{p}': float(np.percentile(x, p)) for p in (50, 90, 99, 99.9)}}


def main():
    trace, run = Path(sys.argv[1]), Path(sys.argv[2])
    real = np.genfromtxt(trace / 'closed_loop/real_rounds.csv', delimiter=',', names=True, dtype=np.int64)
    sim = np.genfromtxt(run / 'mqsim_rounds.txt', names=True, dtype=np.int64)
    # sim rounds are logged per worker in order; select the same rounds on the real side (first k per worker)
    order = np.lexsort((real['read_begin_ns'], real['worker']))
    real = real[order]
    keep = np.zeros(len(real), dtype=bool)
    # map MQSim worker index (order of first appearance in schedule = sorted worker ids) to real worker id
    worker_ids = sorted(np.unique(real['worker']).tolist())
    for idx, wid in enumerate(worker_ids):
        cnt = int((sim['worker'] == idx).sum())
        pos = np.flatnonzero(real['worker'] == wid)[:cnt]
        keep[pos] = True
    real = real[keep]
    t0 = real['read_begin_ns'].min()
    r_round = (real['read_end_ns'] - real['read_begin_ns']) / 1e3
    s_round = (sim['end_ns'] - sim['start_ns']) / 1e3
    r_span = (real['read_end_ns'].max() - t0) / 1e9
    s_span = sim['end_ns'].max() / 1e9

    def query_spans(w, q, b, e):
        key = w.astype(np.int64) * 10**9 + q
        u, inv = np.unique(key, return_inverse=True)
        first = np.full(len(u), np.iinfo(np.int64).max); last = np.zeros(len(u), dtype=np.int64)
        np.minimum.at(first, inv, b); np.maximum.at(last, inv, e)
        return (last - first) / 1e3
    real_widx = np.searchsorted(worker_ids, real['worker'])
    r_q = query_spans(real_widx, real['query'], real['read_begin_ns'], real['read_end_ns'])
    s_q = query_spans(sim['worker'], sim['query'], sim['start_ns'], sim['end_ns'])

    rreq = np.loadtxt(trace / 'mqsim_replay/real_requests.csv', delimiter=',', skiprows=1, dtype=np.int64)
    sreq = np.loadtxt(run / 'mqsim_requests.txt', skiprows=1, dtype=np.int64)
    rreq = rreq[(rreq[:, 0] >= t0) & (rreq[:, 0] <= real['read_end_ns'].max())]
    r_lat = (rreq[:, 1] - rreq[:, 0]) / 1e3; s_lat = (sreq[:, 2] - sreq[:, 0]) / 1e3
    n_reads = int(real['n'].sum())
    res = {
        'rounds_real': int(len(real)), 'rounds_sim': int(len(sim)), 'queries': int(len(s_q)),
        'reads_in_rounds': n_reads, 'reads_sim': int(len(sreq)),
        'span_s': {'real': r_span, 'sim': s_span, 'sim_over_real': s_span / r_span},
        'read_iops': {'real': n_reads / r_span, 'sim': len(sreq) / s_span},
        'query_rate_per_s': {'real': len(r_q) / r_span, 'sim': len(s_q) / s_span},
        'round_time_us': {'real': st(r_round), 'sim': st(s_round)},
        'query_io_span_us': {'real': st(r_q), 'sim': st(s_q)},
        'request_latency_us': {'real': st(r_lat), 'sim': st(s_lat)},
    }
    (run / 'comparison.json').write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))
    try:
        import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 4, figsize=(20, 4.5))
        for i, (title, rv, sv) in enumerate((('request latency (us)', r_lat, s_lat), ('round time: read_begin->read_end (us)', r_round, s_round),
                                             ('query I/O span (us)', r_q, s_q))):
            for v, lab in ((rv, 'T500'), (sv, 'MQSim closed loop')):
                x = np.sort(v); ax[i].plot(x, np.arange(1, len(x) + 1) / len(x), label=lab)
            ax[i].set_xscale('log'); ax[i].set_xlabel(title); ax[i].set_ylabel('CDF'); ax[i].grid(alpha=.3); ax[i].legend()
        bin_ns = 10_000_000
        for arr, comp, lab in ((rreq[:, 0] - t0, rreq[:, 1] - t0, 'T500'), (sreq[:, 0], sreq[:, 2], 'MQSim closed loop')):
            nb = int(comp.max() // bin_ns) + 1
            busy = np.zeros(nb + 1)
            sb, eb = arr // bin_ns, comp // bin_ns
            same = sb == eb
            np.add.at(busy, sb[same], (comp - arr)[same])
            np.add.at(busy, sb[~same], (sb[~same] + 1) * bin_ns - arr[~same])
            np.add.at(busy, eb[~same], comp[~same] - eb[~same] * bin_ns)
            ax[3].plot(np.arange(nb) * bin_ns / 1e9, busy[:nb] / bin_ns, label=lab)
        ax[3].set_xlabel('time (s)'); ax[3].set_ylabel('mean in-flight requests per 10 ms'); ax[3].grid(alpha=.3); ax[3].legend()
        fig.tight_layout(); fig.savefig(run / 'comparison.png', dpi=120)
    except ImportError:
        pass


if __name__ == '__main__':
    main()
