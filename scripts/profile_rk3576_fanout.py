#!/usr/bin/env python3
"""Read-only per-core, per-thread and NPU sampler for the fan-out process."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time


def read(path: Path):
    try:
        return path.read_text().strip()
    except (OSError, ValueError):
        return None


def cpu_ticks():
    rows = {}
    for line in Path('/proc/stat').read_text().splitlines():
        fields = line.split()
        if fields and fields[0].startswith('cpu') and fields[0][3:].isdigit():
            values = [int(x) for x in fields[1:]]
            rows[fields[0]] = (sum(values), values[3] + (values[4] if len(values) > 4 else 0))
    return rows


def task_ticks(pid: int):
    rows = {}
    for task in Path(f'/proc/{pid}/task').glob('[0-9]*'):
        stat = read(task / 'stat')
        if stat is None:
            continue
        # comm is parenthesized and may contain spaces, so split after its final ')'.
        fields = stat.rsplit(')', 1)[-1].split()
        try:
            rows[int(task.name)] = {'ticks': int(fields[11]) + int(fields[12]),
                                    'comm': read(task / 'comm'), 'cpu': int(fields[36])}
        except (ValueError, IndexError):
            continue
    return rows


def sample(prev, now, dt, hz):
    cores = {}
    for name, (total, idle) in now['cores'].items():
        if name in prev['cores']:
            old_total, old_idle = prev['cores'][name]
            delta = total - old_total
            cores[name] = round(100 * (delta - (idle - old_idle)) / delta, 1) if delta else None
    threads = []
    for tid, info in now['tasks'].items():
        old = prev['tasks'].get(tid)
        if old:
            threads.append({'tid': tid, 'name': info['comm'], 'last_cpu': info['cpu'],
                            'cpu_pct_one_core': round(100 * (info['ticks'] - old['ticks']) / hz / dt, 1)})
    return cores, sorted(threads, key=lambda x: x['cpu_pct_one_core'], reverse=True)


def snapshot(pid):
    return {'cores': cpu_ticks(), 'tasks': task_ticks(pid)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--duration', type=float, default=300)
    parser.add_argument('--interval', type=float, default=2)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.pid <= 0 or args.interval <= 0 or args.duration <= 0:
        parser.error('pid, interval, and duration must be positive')
    if not Path(f'/proc/{args.pid}').exists():
        parser.error('process does not exist')
    npu = Path('/sys/class/devfreq/27700000.npu')
    hz = os.sysconf('SC_CLK_TCK')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    start = previous_at = time.monotonic()
    previous = snapshot(args.pid)
    with args.output.open('x', encoding='utf-8') as out:
        while time.monotonic() - start < args.duration:
            time.sleep(args.interval)
            now_at = time.monotonic()
            if not Path(f'/proc/{args.pid}').exists():
                break
            current = snapshot(args.pid)
            cores, threads = sample(previous, current, now_at - previous_at, hz)
            freqs = {f'cpu{i}': read(Path(f'/sys/devices/system/cpu/cpu{i}/cpufreq/scaling_cur_freq'))
                     for i in range(8)}
            temps = [int(value) / 1000 for path in Path('/sys/class/thermal').glob('thermal_zone*/temp')
                     if (value := read(path)) and value.lstrip('-').isdigit()]
            record = {'elapsed_s': round(now_at - start, 2), 'pid': args.pid,
                      'core_util_pct': cores, 'core_freq_khz': freqs,
                      'threads': threads, 'npu_freq_hz': read(npu / 'cur_freq'),
                      'npu_load_raw': read(npu / 'load'),
                      'max_temp_c': max(temps) if temps else None}
            out.write(json.dumps(record) + '\n')
            out.flush()
            previous, previous_at = current, now_at


if __name__ == '__main__':
    main()
