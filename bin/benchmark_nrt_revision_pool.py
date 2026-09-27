"""Isolated paired revision benchmark: serial versus two baseline workers.

No production output roots are writable through this runner. PRISM windows and
calendar publication remain serial to preserve single ownership of shared windows.
"""
import argparse
import json
import multiprocessing
import os
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from benchmark_nrt_extension import compare, compare_inputs, receipt

from hydro_ops.forcing import nrt_cycle as cycle
from hydro_ops.forcing.gfs_publication import _atomic_json


def dependency_days(days, constrained):
    needed = set()
    for day in days:
        needed.update(day + timedelta(days=offset)
                      for offset in ((-1, 0, 1) if constrained[day] else (0,)))
    return sorted(needed)


def initialize(root, work, as_of, baseline, output):
    global engine
    engine = cycle.RecentNrt(root, work / f'worker-{os.getpid()}', as_of,
                            baseline_root=baseline, output_root=output)


def baseline_task(day):
    started = time.monotonic()
    path, record = engine.baseline_day(day)
    result = {'day': str(day), 'seconds': time.monotonic() - started,
              'input_fingerprint': record['input_fingerprint'], 'path': str(path)}
    print(json.dumps({'stage': 'benchmark_baseline_complete', **result}), flush=True)
    return result


def run_arm(root, campaign, work, days, as_of, workers):
    arm = campaign / f'workers-{workers}'
    baseline, output = arm / 'baseline', arm / 'nrt'
    started = time.monotonic()
    final = cycle.RecentNrt(root, work / 'final', as_of,
                           baseline_root=baseline, output_root=output)
    constrained = {d: all(p.is_file() for p in final.prism_paths(d)) for d in days}
    required = dependency_days(days, constrained)
    phase = time.monotonic()
    if workers == 1:
        initialize(root, work, as_of, baseline, output)
        records = [baseline_task(d) for d in required]
    else:
        with ProcessPoolExecutor(max_workers=workers,
                                 mp_context=multiprocessing.get_context('spawn'),
                                 initializer=initialize,
                                 initargs=(root, work, as_of, baseline, output)) as pool:
            records = list(pool.map(baseline_task, required))
    baseline_seconds = time.monotonic() - phase
    frozen = {str(p): cycle.identity(p) for p in (cycle.day_path(baseline, d) for d in required)}
    phase = time.monotonic()
    publications = []
    for day in reversed(days):
        result = final.produce_day(day)
        if result['prism_constrained'] != constrained[day]:
            raise ValueError('PRISM availability changed during benchmark')
        publications.append(result)
        if any(cycle.identity(Path(p)) != old for p, old in frozen.items()):
            raise ValueError('Baseline changed after preparation; reject timing trial')
    report = {'status': 'passed', 'workers': workers, 'baseline_days': records,
              'baseline_seconds': baseline_seconds,
              'reconciliation_publication_seconds': time.monotonic() - phase,
              'total_seconds': time.monotonic() - started, 'days': publications}
    _atomic_json(arm / 'timing.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', type=date.fromisoformat, required=True)
    parser.add_argument('--end', type=date.fromisoformat, required=True)
    parser.add_argument('--campaign', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    campaign = args.campaign.resolve()
    campaign.relative_to(root / 'forcing/work')
    if not 0 <= (args.end - args.start).days <= 6:
        raise ValueError('Benchmark must contain one through seven days')
    campaign.mkdir(parents=True, exist_ok=False)
    # Explicitly prevent use of any persistent production PRISM cache. In-worker
    # adjacent-window reuse remains enabled, independently for each trial.
    os.environ['HYDRO_OPS_NRT_WINDOW_CACHE'] = ''
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}")
    scratch.mkdir(parents=True, exist_ok=True)
    as_of = datetime.now(UTC)
    days = [args.start + timedelta(days=i) for i in range((args.end - args.start).days + 1)]
    report = {'status': 'running', 'job': os.environ['SLURM_JOB_ID'],
              'as_of': as_of.isoformat(), 'start': str(args.start), 'end': str(args.end),
              'scope': 'cold baseline preparation plus serial PRISM publication', 'trials': {}}
    target = campaign / 'acceptance.json'
    _atomic_json(target, report)
    try:
        for workers in (1, 2):
            with tempfile.TemporaryDirectory(prefix=f'revision-{workers}-', dir=scratch) as tmp:
                report['trials'][str(workers)] = run_arm(root, campaign, Path(tmp), days, as_of, workers)
            _atomic_json(target, report)
        started = time.monotonic()
        reference, candidate = campaign / 'workers-1', campaign / 'workers-2'
        baselines = sorted((reference / 'baseline').glob('*/*/*.LDASIN_DOMAIN1'))
        if [p.relative_to(reference / 'baseline') for p in baselines] != [
                p.relative_to(candidate / 'baseline') for p in sorted((candidate / 'baseline').glob('*/*/*.LDASIN_DOMAIN1'))]:
            raise ValueError('Baseline coverage differs')
        for left in baselines:
            right = candidate / 'baseline' / left.relative_to(reference / 'baseline')
            a, b = (cycle.read_json(receipt(p)) for p in (left, right))
            if a['input_fingerprint'] != b['input_fingerprint']:
                raise ValueError(f'Source inputs changed between trials: {left.name}')
            compare(left, right)
        for day in days:
            left, right = (cycle.day_path(p / 'nrt', day) for p in (reference, candidate))
            compare_inputs(cycle.read_json(receipt(right)), cycle.read_json(receipt(left)))
            compare(left, right)
        report.update(status='passed', exact_values_verified=True,
                      comparison_seconds=time.monotonic() - started,
                      speedup=report['trials']['1']['total_seconds'] / report['trials']['2']['total_seconds'])
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        report['finished_utc'] = datetime.now(UTC).isoformat()
        _atomic_json(target, report)
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
