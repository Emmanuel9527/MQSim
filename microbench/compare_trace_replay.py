#!/usr/bin/env python3
"""Compare real per-request latency (block_rq_issue -> complete) with an MQSim open-loop replay of the same trace.

Open-loop caveat: arrivals follow the real issue times, so throughput is identical by construction; only latency,
queueing and in-flight counts are comparable. A slower model builds extra queue that a closed-loop host would not.

Writes <run>/comparison.json and <run>/comparison.png (latency CDF and 10 ms time series).
"""
import json, sys
from pathlib import Path
import numpy as np


def stats(x):
    return {'n': int(len(x)), 'mean_us': float(x.mean()),
            **{f'p{p}_us': float(np.percentile(x, p)) for p in (50, 90, 99, 99.9)}, 'max_us': float(x.max())}


def main():
    replay, run = Path(sys.argv[1]), Path(sys.argv[2])
    real = np.loadtxt(replay / 'real_requests.csv', delimiter=',', skiprows=1, dtype=np.int64)
    sim = np.loadtxt(run / 'mqsim_requests.txt', skiprows=1, dtype=np.int64)
    if sim.ndim == 1: sim = sim[None, :]
    t0 = real[:, 0].min()
    r_arr, r_lat = real[:, 0] - t0, (real[:, 1] - real[:, 0]) / 1000
    s_arr, s_lat = sim[:, 0], (sim[:, 2] - sim[:, 0]) / 1000
    ro, so = np.lexsort((real[:, 2], r_arr)), np.lexsort((sim[:, 3], s_arr))
    paired = len(ro) == len(so) and np.array_equal(r_arr[ro], s_arr[so])
    result = {'real': stats(r_lat), 'sim': stats(s_lat), 'requests_real': int(len(real)), 'requests_sim': int(len(sim)),
              'arrivals_identical': bool(paired)}
    if paired:
        rl, sl = r_lat[ro], s_lat[so]
        result['per_request'] = {'pearson_r': float(np.corrcoef(rl, sl)[0, 1]),
                                 'median_sim_over_real': float(np.median(sl / rl)),
                                 'mean_abs_diff_us': float(np.abs(sl - rl).mean())}
    # 10 ms bins: completions and mean in-flight (sum of overlap / bin width)
    bin_ns = 10_000_000
    nb = int(max(r_arr.max(), s_arr.max()) // bin_ns) + 1

    def series(arr, lat_us):
        comp = arr + (lat_us * 1000).astype(np.int64)
        inflight = np.zeros(nb + 2)
        # exact overlap of each request with each bin
        start_bin, end_bin = arr // bin_ns, np.minimum(comp // bin_ns, nb + 1)
        same = start_bin == end_bin
        np.add.at(inflight, start_bin[same], (comp - arr)[same])
        diff = ~same
        np.add.at(inflight, start_bin[diff], ((start_bin[diff] + 1) * bin_ns - arr[diff]))
        np.add.at(inflight, end_bin[diff], (comp[diff] - end_bin[diff] * bin_ns))
        for s, e in zip(start_bin[diff], end_bin[diff]):
            if e > s + 1: inflight[s + 1:e] += bin_ns
        meanlat = np.bincount(arr // bin_ns, weights=lat_us, minlength=nb) / np.maximum(np.bincount(arr // bin_ns, minlength=nb), 1)
        return inflight[:nb] / bin_ns, meanlat[:nb]

    r_inf, r_ml = series(r_arr, r_lat); s_inf, s_ml = series(s_arr, s_lat)
    result['mean_inflight_real'] = float(r_inf.mean()); result['mean_inflight_sim'] = float(s_inf.mean())
    result['bin_mean_latency_corr'] = float(np.corrcoef(r_ml, s_ml)[0, 1])
    (run / 'comparison.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    try:
        import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 3, figsize=(16, 4.5))
        for lat, lab in ((r_lat, 'T500 (block layer)'), (s_lat, 'MQSim replay')):
            x = np.sort(lat); ax[0].plot(x, np.arange(1, len(x) + 1) / len(x), label=lab)
        ax[0].set_xscale('log'); ax[0].set_xlabel('request latency (us)'); ax[0].set_ylabel('CDF'); ax[0].legend(); ax[0].grid(alpha=.3)
        t = np.arange(nb) * bin_ns / 1e9
        ax[1].plot(t, r_ml, label='T500'); ax[1].plot(t, s_ml, label='MQSim'); ax[1].set_xlabel('time (s)')
        ax[1].set_ylabel('mean latency per 10 ms (us)'); ax[1].legend(); ax[1].grid(alpha=.3)
        ax[2].plot(t, r_inf, label='T500'); ax[2].plot(t, s_inf, label='MQSim'); ax[2].set_xlabel('time (s)')
        ax[2].set_ylabel('mean in-flight requests per 10 ms'); ax[2].legend(); ax[2].grid(alpha=.3)
        fig.tight_layout(); fig.savefig(run / 'comparison.png', dpi=120)
    except ImportError:
        pass


if __name__ == '__main__':
    main()
