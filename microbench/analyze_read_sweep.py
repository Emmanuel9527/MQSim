#!/usr/bin/env python3
"""Analyze completed fio archives without fitting or modifying MQSim."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import statistics as stats
import tempfile
import time

from read_sweep import summarize


METRICS = ('iops', 'lat_mean_us', 'lat_p50_us', 'lat_p95_us', 'lat_p99_us',
           'slat_mean_us', 'clat_mean_us')


def write_csv(path, rows):
    if not rows:
        return
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def verify_archive(root):
    if not (root / 'COMPLETE').is_file() or (root / 'INCOMPLETE').exists():
        raise ValueError('Run is incomplete; refuse to report it as a completed sweep')
    manifest = json.loads((root / 'archive_manifest.json').read_text())
    required = {'metadata.json', 'COMPLETE'}
    if not required.issubset(manifest['sha256']):
        raise ValueError('Archive manifest does not cover required metadata')
    for relative, expected in manifest['sha256'].items():
        path = root / relative
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Manifest path escapes archive')
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != expected:
            raise ValueError(f'Archive checksum mismatch: {relative}')
    return manifest


def aggregate(rows):
    result = []
    for qd in sorted({r['requested_qd'] for r in rows}):
        subset = [r for r in rows if r['requested_qd'] == qd]
        group = {'requested_qd': qd, 'repeats': len(subset)}
        for metric in METRICS:
            values = [r[metric] for r in subset]
            if any(v is None for v in values):
                raise ValueError(f'Missing {metric}; verify fio percentile configuration')
            group[metric + '_median'] = stats.median(values)
            group[metric + '_min'] = min(values)
            group[metric + '_max'] = max(values)
        group['iops_max_min_ratio'] = group['iops_max'] / group['iops_min']
        result.append(group)
    baseline = next((g['iops_median'] for g in result if g['requested_qd'] == 1), None)
    previous = None
    for group in result:
        group['iops_relative_to_qd1'] = group['iops_median'] / baseline if baseline else None
        group['iops_ratio_to_previous_qd'] = group['iops_median'] / previous if previous else None
        previous = group['iops_median']
    return result


def device_intervals(source):
    """Linux block-stat deltas; not attributed to fio and not NAND counters."""
    result, previous = [], None
    for line in source.read_text().splitlines():
        record = json.loads(line)
        fields = record['block_stat'].split()
        if len(fields) < 11 or not all(v.isdigit() for v in fields):
            previous = None
            continue
        values = list(map(int, fields))
        if previous:
            old, old_values = previous
            seconds = (record['monotonic_ns'] - old['monotonic_ns']) / 1e9
            counters = (0, 2, 3, 4, 6, 7, 9, 10)
            delta = {k: values[k] - old_values[k] for k in counters}
            if seconds > 0 and all(v >= 0 for v in delta.values()):
                temperatures = [float(v) / 1000 for v in record.get('temperatures_millicelsius', {}).values()
                                if str(v).lstrip('-').isdigit()]
                result.append({
                    'end_wall_time_ns': record['wall_time_ns'],
                    'end_monotonic_ns': record['monotonic_ns'], 'interval_s': seconds,
                    'device_read_iops': delta[0] / seconds,
                    'device_write_iops': delta[4] / seconds,
                    'device_read_mib_s': delta[2] * 512 / seconds / 1024**2,
                    'device_write_mib_s': delta[6] * 512 / seconds / 1024**2,
                    'device_read_await_ms': delta[3] / delta[0] if delta[0] else None,
                    'device_weighted_outstanding': delta[10] / (seconds * 1000),
                    'device_busy_pct': delta[9] / (seconds * 10),
                    'instantaneous_inflight': values[8],
                    'max_reported_nvme_sensor_c': max(temperatures) if temperatures else None,
                })
        previous = record, values
    return result


def plot(rows, groups, output):
    os.environ.setdefault('MPLCONFIGDIR', tempfile.mkdtemp(prefix='mqsim-mpl-'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    panels = [('iops', 'Read IOPS'), ('lat_mean_us', 'Mean fio total latency (us)'),
              ('lat_p99_us', 'Per-run p99 total latency (us)'), ('slat_mean_us', 'Mean fio submission latency (us)')]
    x = [g['requested_qd'] for g in groups]
    for ax, (metric, title) in zip(axes.flat, panels):
        ax.scatter([r['requested_qd'] for r in rows], [r[metric] for r in rows],
                   alpha=.5, label='Every repetition')
        median = [g[metric + '_median'] for g in groups]
        ax.errorbar(x, median, yerr=[[g[metric + '_median'] - g[metric + '_min'] for g in groups],
                                   [g[metric + '_max'] - g[metric + '_median'] for g in groups]],
                    fmt='o-', capsize=4, label='Median and min-max (not CI)')
        ax.set_xscale('log', base=2)
        ax.set_xticks(x, labels=[str(v) for v in x])
        ax.set_xlabel('Requested fio QD (not measured device QD)')
        ax.set_ylabel(title)
        ax.grid(alpha=.25)
    axes[0, 0].legend(fontsize=8)
    fig.suptitle('Real SSD: all repetitions retained; no MQSim fit')
    fig.tight_layout()
    fig.savefig(output / 'qd_curves.png', dpi=160)
    fig.savefig(output / 'qd_curves.svg')
    plt.close(fig)


def analyze(root, output):
    manifest = verify_archive(root)
    metadata = json.loads((root / 'metadata.json').read_text())
    rows = []
    labels = set()
    for item in metadata['schedule']:
        label = f'r{item["repeat"]}_qd{item["qd"]}'
        if label in labels:
            raise ValueError('Duplicate schedule entry')
        labels.add(label)
        relative = label + '/fio.json'
        if relative not in manifest['sha256']:
            raise ValueError(f'Unverified raw result: {relative}')
        doc = json.loads((root / relative).read_text())
        rows.append(summarize(doc, label, item['qd'], item['repeat']))
    if not rows:
        raise ValueError('Empty sweep')
    groups = aggregate(rows)
    output.mkdir(parents=True, exist_ok=False)
    write_csv(output / 'per_run.csv', rows)
    write_csv(output / 'by_qd.csv', groups)
    if (root / 'system_samples.jsonl').exists():
        write_csv(output / 'device_intervals.csv', device_intervals(root / 'system_samples.jsonl'))
    flags = []
    for group in groups:
        if group['repeats'] < 3:
            flags.append(f'QD={group["requested_qd"]}: fewer than 3 repeats; insufficient repeatability evidence.')
        if group['iops_max_min_ratio'] > 1.2:
            flags.append(f'QD={group["requested_qd"]}: IOPS max/min={group["iops_max_min_ratio"]:.2f}; inspect states/background traffic. No samples removed.')
    if metadata['arguments']['runtime'] < 30:
        flags.append('Measured duration <30 s: short run; treat as a functional check, not a calibration baseline.')
    plot(rows, groups, output)
    lines = ['# Real-device QD baseline', '', f'Source: `{root}`', '',
             'Checksums verified. Statistics recomputed from raw fio JSON, not summary.csv.', '',
             '| Requested QD | Repeats | Median IOPS | Min–max IOPS | Median mean lat (us) | Median per-run p99 (us) |',
             '|---:|---:|---:|---:|---:|---:|']
    for g in groups:
        lines.append(f'| {g["requested_qd"]} | {g["repeats"]} | {g["iops_median"]:.1f} | {g["iops_min"]:.1f}–{g["iops_max"]:.1f} | {g["lat_mean_us_median"]:.2f} | {g["lat_p99_us_median"]:.2f} |')
    lines += ['', '## Review flags', ''] + (['- ' + flag for flag in flags] or ['No flags under the simple duration/repetition/spread heuristics. This is not proof of stability.'])
    lines += ['', '## Interpretation limits', '',
              '- Min–max bars are not confidence intervals. Per-run p99 medians are not pooled p99.',
              '- 1.2x spread is a review flag, not an exclusion or calibration acceptance rule.',
              '- fio lat/clat include host effects; requested QD is not measured device QD.',
              '- device_intervals.csv includes all device traffic and warmup/setup; no automatic attribution to measured fio intervals.',
              '- Raw per-second logs remain in the archive. Inspect within-run changes before fitting.',
              '- No saturation point or hardware topology is inferred automatically.',
              '- No MQSim parameters or measurements were modified; this report is not a calibration claim.', '']
    (output / 'report.md').write_text('\n'.join(lines))
    print(f'Analysis saved: {output}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path, help='Completed persistent archive (not the active tmpfs run)')
    parser.add_argument('--out', type=Path, help='New output directory; defaults to run/analysis')
    parser.add_argument('--wait', action='store_true', help='Wait for the final archive directory to appear')
    parser.add_argument('--timeout', type=float, default=3600)
    args = parser.parse_args()
    if args.wait:
        deadline = time.monotonic() + args.timeout
        while not args.run.exists():
            if time.monotonic() >= deadline:
                raise SystemExit('Timed out; no results analyzed')
            time.sleep(5)
    analyze(args.run, args.out or args.run / 'analysis')


if __name__ == '__main__':
    main()
