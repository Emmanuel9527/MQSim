#!/usr/bin/env python3
"""SSD-internal time breakdown from an MQSim run with MQSIM_TRANSACTION_LOG (one line per flash read).

Per parallel unit (channel, chip, die; with the calibrated T500 config each unit is one plane modelled as a virtual
LUN) the analysis window is split into:
  array_read     die executing the array read (tR)                         exec_start -> exec_end
  held           die occupied but not reading the array: command transfer, data waiting for the bus, data-out
                 (cmd_start -> exec_start, exec_end -> xfer_start, xfer_start -> xfer_end)
  idle_queued    die free while a read for it waits in the TSU                issue -> cmd_start, not occupied
  idle_empty     die free and nothing queued for it
Per channel: command transfer, data transfer, idle. Whole SSD: time with no flash read anywhere in flight, and the
time-weighted distribution of how many units execute array reads concurrently.

Checks reported in the output: per-unit occupied intervals and per-channel bus intervals must not overlap; data-out and
array-read durations are listed so they can be compared with the configured timing.
Usage: analyze_ssd_slack.py <run_dir> [--trim 0.05]   (trim = fraction of the span dropped at each end)
Writes <run_dir>/slack.json and slack.png.
"""
import argparse, json
from pathlib import Path
import numpy as np


def measure_by_state(starts_a, ends_a, starts_b, ends_b, lo, hi):
    """Durations inside [lo, hi] for the four states (a>0?, b>0?) of two interval multisets."""
    t = np.concatenate([starts_a, ends_a, starts_b, ends_b, [lo, hi]])
    da = np.concatenate([np.ones(len(starts_a)), -np.ones(len(ends_a)), np.zeros(len(starts_b) * 2 + 2)])
    db = np.concatenate([np.zeros(len(starts_a) * 2), np.ones(len(starts_b)), -np.ones(len(ends_b)), [0, 0]])
    o = np.argsort(t, kind='stable'); t, da, db = t[o], da[o], db[o]
    ca, cb = np.cumsum(da), np.cumsum(db)
    dur = np.diff(t)
    left, right = np.clip(t[:-1], lo, hi), np.clip(t[1:], lo, hi)
    dur = right - left
    a, b = ca[:-1] > 0, cb[:-1] > 0
    return {(x, y): float(dur[(a == x) & (b == y)].sum()) for x in (False, True) for y in (False, True)}


def overlaps(starts, ends):
    o = np.argsort(starts); s, e = starts[o], ends[o]
    return int((s[1:] < e[:-1]).sum())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('run', type=Path)
    ap.add_argument('--trim', type=float, default=0.05)
    a = ap.parse_args()
    run = a.run.resolve()
    x = np.loadtxt(run / 'mqsim_transactions.txt', skiprows=1, dtype=np.int64)
    issue, cmd, es, ee, xs, xe, ch, chip, die = (x[:, i] for i in range(9))
    if (cmd == 0).any() or (es == 0).any() or (xe == 0).any(): raise SystemExit('Incomplete transaction timelines')
    span0, span1 = issue.min(), xe.max()
    lo, hi = span0 + a.trim * (span1 - span0), span1 - a.trim * (span1 - span0)
    W = hi - lo
    unit = (ch * 1000 + chip) * 1000 + die
    units = np.unique(unit)
    totals = dict(array_read=0.0, held=0.0, idle_queued=0.0, idle_empty=0.0)
    per_unit_read, unit_overlaps = [], 0
    for u in units:
        m = unit == u
        occ_s, occ_e = cmd[m], xe[m]
        unit_overlaps += overlaps(occ_s, occ_e)
        # occupied vs queued
        st = measure_by_state(occ_s, occ_e, issue[m], cmd[m], lo, hi)
        busy = st[(True, False)] + st[(True, True)]
        totals['idle_queued'] += st[(False, True)]
        totals['idle_empty'] += st[(False, False)]
        rd = measure_by_state(es[m], ee[m], np.array([], dtype=np.int64), np.array([], dtype=np.int64), lo, hi)
        r = rd[(True, False)]
        totals['array_read'] += r
        totals['held'] += busy - r
        per_unit_read.append(r / W)
    unit_time = W * len(units)
    breakdown = {k: v / unit_time for k, v in totals.items()}
    chans, ch_over = {}, 0
    for c in np.unique(ch):
        m = ch == c
        bs, be = np.concatenate([cmd[m], xs[m]]), np.concatenate([es[m], xe[m]])
        ch_over += overlaps(bs, be)
        cs = measure_by_state(cmd[m], es[m], xs[m], xe[m], lo, hi)
        chans[int(c)] = {'cmd': (cs[(True, False)] + cs[(True, True)]) / W, 'data': (cs[(False, True)] + cs[(True, True)]) / W}
    # whole SSD
    allst = measure_by_state(issue, xe, np.array([], dtype=np.int64), np.array([], dtype=np.int64), lo, hi)
    no_work = allst[(False, False)] / W
    t = np.concatenate([es, ee, [lo, hi]]); d = np.concatenate([np.ones(len(es)), -np.ones(len(ee)), [0, 0]])
    o = np.argsort(t, kind='stable'); t, d = t[o], d[o]; c = np.cumsum(d)[:-1]
    dur = np.clip(t[1:], lo, hi) - np.clip(t[:-1], lo, hi)
    hist = np.bincount(c.astype(int), weights=dur) / W
    inw = (es >= lo) & (es <= hi)
    res = {
        'run': str(run), 'transactions': int(len(x)), 'units': int(len(units)), 'window_s': W / 1e9,
        'unit_time_breakdown': breakdown,
        'per_unit_array_read_fraction': {'min': float(min(per_unit_read)), 'mean': float(np.mean(per_unit_read)),
                                         'max': float(max(per_unit_read))},
        'channel_utilization': chans,
        'ssd_no_read_in_flight_fraction': no_work,
        'concurrent_array_reads': {'mean': float((np.arange(len(hist)) * hist).sum()),
                                   'p50': int(np.searchsorted(np.cumsum(hist), 0.5)),
                                   'p90': int(np.searchsorted(np.cumsum(hist), 0.9)),
                                   'max': int(len(hist) - 1)},
        'checks': {'unit_occupied_overlaps': unit_overlaps, 'channel_bus_overlaps': ch_over,
                   'array_read_ns': [int((ee - es)[inw].min()), int((ee - es)[inw].max())],
                   'data_out_ns': [int((xe - xs)[inw].min()), int((xe - xs)[inw].max())],
                   'command_ns': [int((es - cmd)[inw].min()), int((es - cmd)[inw].max())]},
        'per_read_waits_us': {'queued_before_dispatch_mean': float((cmd - issue)[inw].mean() / 1e3),
                              'data_waiting_for_bus_mean': float((xs - ee)[inw].mean() / 1e3),
                              'data_waiting_for_bus_p99': float(np.percentile((xs - ee)[inw], 99) / 1e3)},
    }
    (run / 'slack.json').write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))
    try:
        import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 2, figsize=(12, 4))
        labels = ['array_read', 'held', 'idle_queued', 'idle_empty']
        ax[0].bar(labels, [100 * breakdown[k] for k in labels]); ax[0].set_ylabel('% of unit time'); ax[0].grid(axis='y', alpha=.3)
        ax[0].set_title(f'{len(units)} parallel units')
        ax[1].bar(np.arange(len(hist)), 100 * hist); ax[1].set_xlabel('units executing array reads concurrently')
        ax[1].set_ylabel('% of time'); ax[1].grid(axis='y', alpha=.3)
        fig.tight_layout(); fig.savefig(run / 'slack.png', dpi=120)
    except ImportError:
        pass


if __name__ == '__main__':
    main()
