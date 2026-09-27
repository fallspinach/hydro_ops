"""Isolated staged revision benchmark; 2/1/1 reference versus 4/4/2 candidate."""
import argparse
import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from benchmark_nrt_extension import compare, compare_inputs, receipt
from benchmark_nrt_revision_pool import baseline_task, dependency_days, initialize, run_arm

from hydro_ops.forcing import nrt_cycle as cycle
from hydro_ops.forcing.gfs_publication import _atomic_json


def window_days(days, constrained):
    return sorted({d + timedelta(days=i) for d in days if constrained[d] for i in (0, 1)})


class PreparedNrt(cycle.RecentNrt):
    require_prepared_windows = True

    def baseline_day(self, day, **kwargs):
        path = cycle.day_path(self.baseline, day)
        record = cycle.read_json(receipt(path))
        if record.get('status') != 'passed' or record.get('published_identity') != cycle.identity(path):
            raise ValueError(f'Prepared baseline missing or stale: {day}')
        # Read-only consumption: never rebuild a shared dependency here.
        for source in record['inputs']['files'] + record['inputs']['assets']:
            if cycle.identity(Path(source['path'])) != source:
                raise ValueError(f'Source changed after preparation: {source["path"]}')
        return path, record


def initialize_prepared(root, work, as_of, baseline, output, windows):
    global prepared
    private = work / f'publisher-{os.getpid()}'
    private.mkdir(parents=True, exist_ok=True)
    (private / 'nrt-prism-windows').symlink_to(windows.parent, target_is_directory=True)
    prepared = PreparedNrt(root, private, as_of, baseline_root=baseline, output_root=output)


def final_task(day):
    started = time.monotonic()
    result = prepared.produce_day(day)
    result['seconds'] = time.monotonic() - started
    print(json.dumps({'stage': 'calendar_publication', **result}), flush=True)
    return result


def window_task(task):
    root, work, as_of, baseline, windows, day = task
    started = time.monotonic()
    records = []
    for d in (day - timedelta(days=1), day):
        path = cycle.day_path(baseline, d)
        record = cycle.read_json(receipt(path))
        if record.get('status') != 'passed' or record.get('published_identity') != cycle.identity(path):
            raise ValueError('Window baseline receipt invalid')
        records.append((path, record))
    identities = [cycle.identity(p) for p, _ in records]
    prism_root = root / 'forcing/inputs/oregon_state/prism/an/4km/daily'
    prism = [prism_root / v / f'{day:%Y/%m}/prism_{v}_us_25m_{day:%Y%m%d}.nc'
             for v in ('ppt', 'tmin', 'tmax')]
    prism_ids = [cycle.identity(p) for p in prism]
    if any(p.get('missing') for p in prism_ids):
        raise ValueError('Window PRISM input missing')
    env = cycle.reconciliation_environment(cycle.configuration(root), os.environ)
    revision = 'early' if (as_of.date() - day).days < 30 else 'provisional'
    signature = cycle.window_signature(day, records, prism, revision,
        env['HYDRO_OPS_ARCHIVE_CHUNKS'] + ':' + env['HYDRO_OPS_ARCHIVE_PRESERVE_SOURCE_CHUNKS'])
    private = work / f'window-{day}'
    private.mkdir(parents=True, exist_ok=False)
    command = [sys.executable, str(root / 'bin/produce_prism_constrained_daily.py'),
               '--day', str(day), '--complete-root', str(baseline), '--output-root', str(windows),
               '--revision', revision, '--stream', 'nrt', '--work-directory', str(private),
               '--archive-access', 'direct', '--allow-legacy-12utc-output', '--force',
               '--baseline-archives', *(str(p) for p, _ in records)]
    subprocess.run(command, cwd=root, env=env, check=True)
    if identities != [cycle.identity(p) for p, _ in records] or prism_ids != [cycle.identity(p) for p in prism]:
        raise ValueError('Window inputs changed while processing')
    path = cycle.day_path(windows, day)
    _atomic_json(path.with_name(path.name + '.reuse.json'),
                 {'signature': signature, 'identity': cycle.identity(path)})
    result = {'day': str(day), 'seconds': time.monotonic() - started}
    print(json.dumps({'stage': 'prepared_window', **result}), flush=True)
    return result


def run_staged(root, arm, work, days, as_of, counts):
    baseline_workers, prism_workers, final_workers = counts
    baseline, output = arm / 'baseline', arm / 'nrt'
    windows = work / 'shared-windows/nrt'
    windows.mkdir(parents=True)
    inspector = cycle.RecentNrt(root, work / 'inspector', as_of,
                               baseline_root=baseline, output_root=output)
    constrained = {d: all(p.is_file() for p in inspector.prism_paths(d)) for d in days}
    required = dependency_days(days, constrained)
    context = multiprocessing.get_context('spawn')
    started = phase = time.monotonic()
    with ProcessPoolExecutor(baseline_workers, mp_context=context, initializer=initialize,
                             initargs=(root, work, as_of, baseline, output)) as pool:
        baselines = list(pool.map(baseline_task, required))
    baseline_seconds = time.monotonic() - phase
    frozen = {str(cycle.day_path(baseline, d)): cycle.identity(cycle.day_path(baseline, d)) for d in required}
    phase = time.monotonic()
    tasks = [(root, work, as_of, baseline, windows, d) for d in window_days(days, constrained)]
    with ProcessPoolExecutor(prism_workers, mp_context=context) as pool:
        window_results = list(pool.map(window_task, tasks))
    window_seconds = time.monotonic() - phase
    phase = time.monotonic()
    with ProcessPoolExecutor(final_workers, mp_context=context, initializer=initialize_prepared,
                             initargs=(root, work, as_of, baseline, output, windows)) as pool:
        results = list(pool.map(final_task, reversed(days)))
    if any(cycle.identity(Path(p)) != old for p, old in frozen.items()):
        raise ValueError('Baseline changed after preparation')
    if any(r['prism_constrained'] != constrained[date.fromisoformat(r['day'])] for r in results):
        raise ValueError('PRISM availability changed')
    return {'workers': counts, 'baseline_seconds': baseline_seconds,
            'window_seconds': window_seconds, 'publication_seconds': time.monotonic() - phase,
            'total_seconds': time.monotonic() - started, 'baselines': baselines,
            'windows': window_results, 'days': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    campaign = args.campaign.resolve()
    campaign.relative_to(root / 'forcing/work')
    campaign.mkdir(parents=True, exist_ok=False)
    os.environ['HYDRO_OPS_NRT_WINDOW_CACHE'] = ''
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}")
    scratch.mkdir(parents=True, exist_ok=True)
    days = [date(2026, 9, d) for d in range(18, 25)]
    as_of = datetime.now(UTC)
    report = {'status': 'running', 'job': os.environ['SLURM_JOB_ID'],
              'as_of': as_of.isoformat(), 'trials': {}}
    stop_monitor = threading.Event()
    scratch_samples = []
    def sample_scratch():
        while True:
            scratch_samples.append(shutil.disk_usage(scratch).free)
            if stop_monitor.wait(15):
                return
    monitor = threading.Thread(target=sample_scratch, daemon=True)
    monitor.start()
    target = campaign / 'acceptance.json'
    _atomic_json(target, report)
    try:
        # Candidate first reverses the previous benchmark's ordering bias.
        for name, counts in [('candidate', (4, 4, 2)), ('reference', (2, 1, 1))]:
            with tempfile.TemporaryDirectory(prefix=name + '-', dir=scratch) as tmp:
                report['trials'][name] = (
                    run_staged(root, campaign / name, Path(tmp), days, as_of, counts)
                    if name == 'candidate' else
                    run_arm(root, campaign / name, Path(tmp), days, as_of, 2))
            _atomic_json(target, report)
        started = time.monotonic()
        a, b = campaign / 'reference/workers-2', campaign / 'candidate'
        if [p.relative_to(a / 'baseline') for p in sorted((a / 'baseline').glob('*/*/*.LDASIN_DOMAIN1'))] != [
                p.relative_to(b / 'baseline') for p in sorted((b / 'baseline').glob('*/*/*.LDASIN_DOMAIN1'))]:
            raise ValueError('Baseline coverage differs')
        for left in sorted((a / 'baseline').glob('*/*/*.LDASIN_DOMAIN1')):
            right = b / 'baseline' / left.relative_to(a / 'baseline')
            if cycle.read_json(receipt(left))['input_fingerprint'] != cycle.read_json(receipt(right))['input_fingerprint']:
                raise ValueError('Baseline source fingerprints differ')
            compare(left, right)
        for day in days:
            left, right = (cycle.day_path(p / 'nrt', day) for p in (a, b))
            compare_inputs(cycle.read_json(receipt(right)), cycle.read_json(receipt(left)))
            compare(left, right)
        report.update(status='passed', exact_values_verified=True,
                      comparison_seconds=time.monotonic() - started,
                      speedup=report['trials']['reference']['total_seconds'] / report['trials']['candidate']['total_seconds'])
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        stop_monitor.set()
        monitor.join()
        report['minimum_observed_scratch_free_bytes'] = min(scratch_samples)
        report['scratch_samples'] = len(scratch_samples)
        report['finished_utc'] = datetime.now(UTC).isoformat()
        _atomic_json(target, report)
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
