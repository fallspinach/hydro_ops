"""Bounded, dependency-owned revision stages; invoked under the NRT cycle lock."""
from __future__ import annotations

import multiprocessing
import os
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

from hydro_ops.forcing import nrt_cycle as cycle


def dependencies(days, constrained):
    return sorted({d + timedelta(days=i) for d in days
                   for i in ((-1, 0, 1) if constrained[d] else (0,))})


def required_windows(days, constrained):
    return sorted({d + timedelta(days=i) for d in days if constrained[d] for i in (0, 1)})


def worker_counts(config):
    names = ('revision_baseline_workers', 'revision_window_workers', 'revision_publication_workers')
    counts = tuple(int(config.get(k, v)) for k, v in zip(names, (4, 4, 2), strict=True))
    if any(not 1 <= n <= maximum for n, maximum in zip(counts, (4, 4, 2), strict=True)):
        raise ValueError('Staged revision worker counts exceed validated bounds (4/4/2)')
    return counts


def prepared_baseline(root, day):
    path = cycle.day_path(root, day)
    record = cycle.read_json(path.with_name(path.name + '.nrt-receipt.json'))
    if record.get('status') != 'passed' or record.get('published_identity') != cycle.identity(path):
        raise ValueError(f'Prepared baseline missing or stale: {day}')
    for source in record['inputs']['files'] + record['inputs']['assets']:
        if cycle.identity(Path(source['path'])) != source:
            raise ValueError(f'Source changed after baseline preparation: {source["path"]}')
    return path, record


class PreparedNrt(cycle.RecentNrt):
    require_prepared_windows = True

    def baseline_day(self, day, **kwargs):
        return prepared_baseline(self.baseline, day)

    def prism_cache(self, reuse=True):
        # Only the window stage owns cache publication. The controller prunes
        # after every window reader/writer has finished.
        return None


def initialize(root, work, as_of, baseline, output, mode, windows):
    global engine, shared_windows
    private = work / f'{mode}-{os.getpid()}'
    private.mkdir(parents=True, exist_ok=True)
    cls = PreparedNrt if mode == 'publish' else cycle.RecentNrt
    if mode == 'publish':
        (private / 'nrt-prism-windows').symlink_to(windows.parent, target_is_directory=True)
    engine = cls(root, private, as_of, baseline_root=baseline, output_root=output)
    shared_windows = windows


def baseline_task(day):
    path = cycle.day_path(engine.baseline, day)
    before = cycle.identity(path)
    started = time.monotonic()
    engine.baseline_day(day)
    return {'day': str(day), 'seconds': time.monotonic() - started,
            'status': 'unchanged' if cycle.identity(path) == before else 'published'}


def window_task(day):
    started = time.monotonic()
    baselines = [prepared_baseline(engine.baseline, d) for d in (day - timedelta(days=1), day)]
    prism = [p for p in engine.prism_paths(day) if f'{day:%Y%m%d}' in p.name]
    before = [cycle.identity(p) for p in prism]
    if any(p.get('missing') for p in before):
        raise ValueError(f'Missing PRISM inputs: {day}')
    env = cycle.reconciliation_environment(engine.config, os.environ)
    timings, writers = [], []
    with tempfile.TemporaryDirectory(prefix=f'window-{day}-', dir=engine.work) as tmp:
        engine.prepare_prism_window(day, baselines, prism, shared_windows, Path(tmp), env,
                                    True, engine.prism_cache(), writers, timings)
    if before != [cycle.identity(p) for p in prism]:
        raise ValueError('PRISM changed during window preparation')
    return {'day': str(day), 'seconds': time.monotonic() - started, 'stage_timings': timings}


def publish_task(day):
    started = time.monotonic()
    record = engine.produce_day(day)
    record['worker_seconds'] = time.monotonic() - started
    return record


def run_pool(parent, work, windows, mode, workers, function, tasks, progress):
    if not tasks:
        return []
    results = []
    with ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context('spawn'),
                             initializer=initialize,
                             initargs=(parent.root, work, parent.as_of, parent.baseline,
                                       parent.output, mode, windows)) as pool:
        futures = {pool.submit(function, d): d for d in tasks}
        try:
            for future in as_completed(futures):
                results.append(future.result())
                progress({'phase': mode, 'completed': len(results), 'total': len(tasks),
                          'last_result': results[-1]})
        except BaseException:
            for future in futures:
                future.cancel()
            raise
    return results


def run_staged_days(parent, days, progress=lambda record: None):
    counts = worker_counts(parent.config)
    env = cycle.reconciliation_environment(parent.config, os.environ)
    if env['HYDRO_OPS_NRT_REUSE_WINDOWS'] != '1':
        raise ValueError('Staged revisions require validated window reuse')
    cpus = int(os.environ.get('SLURM_CPUS_PER_TASK', '128'))
    if cpus < 128:
        raise ValueError('Staged revisions require the validated 128-CPU allocation')
    days = list(dict.fromkeys(days))
    started = time.monotonic()
    constrained = {d: all(p.is_file() for p in parent.prism_paths(d)) for d in days}
    result = {'workers': counts, 'baseline_days': [], 'windows': []}
    # Every phase has a barrier and unique date ownership. No output-dependent
    # workers can build/overwrite their neighbors' baseline/window inputs.
    with tempfile.TemporaryDirectory(prefix='staged-revision-', dir=parent.work) as tmp:
        work = Path(tmp)
        windows = work / 'shared-windows/nrt'
        windows.mkdir(parents=True)
        phase = time.monotonic()
        needed = dependencies(days, constrained)
        result['baseline_days'] = run_pool(parent, work, windows, 'baseline', counts[0],
                                           baseline_task, needed, progress)
        result['baseline_seconds'] = time.monotonic() - phase
        frozen = {d: prepared_baseline(parent.baseline, d) for d in needed}
        identities = {str(p): cycle.identity(p) for p, _ in frozen.values()}
        changed, unchanged = [], []
        for d in days:
            prism = parent.prism_paths(d)
            if all(p.is_file() for p in prism) != constrained[d]:
                raise ValueError('PRISM availability changed during baseline preparation')
            records = [frozen[x] for x in dependencies([d], constrained)]
            path = cycle.day_path(parent.output, d)
            old = cycle.read_json(path.with_name(path.name + '.nrt-receipt.json'))
            if old.get('prism_constrained') and not constrained[d]:
                raise ValueError('Refusing to downgrade PRISM-constrained output')
            if cycle.up_to_date(path, old, cycle.fingerprint(cycle.publication_inputs(records, prism))):
                unchanged.append({'day': str(d), 'status': 'unchanged', 'gfs_hours': old['gfs_hours'],
                                  'prism_constrained': old['prism_constrained'], 'worker_seconds': 0})
            else:
                changed.append(d)
        phase = time.monotonic()
        result['windows'] = run_pool(parent, work, windows, 'window', counts[1], window_task,
                                     required_windows(changed, constrained), progress)
        result['window_seconds'] = time.monotonic() - phase
        phase = time.monotonic()
        published = run_pool(parent, work, windows, 'publish', counts[2], publish_task, changed, progress)
        result['publication_seconds'] = time.monotonic() - phase
        if any(cycle.identity(Path(p)) != old for p, old in identities.items()):
            raise ValueError('Baseline changed during final publication')
        cache = parent.prism_cache()
        if cache:
            result['pruned_windows'] = len(cache.prune(
                max_entries=parent.config.get('window_cache_max_entries', 32),
                max_bytes=parent.config.get('window_cache_max_bytes', 160_000_000_000),
                max_age_days=parent.config.get('window_cache_max_age_days', 14)))
    result['total_seconds'] = time.monotonic() - started
    by_day = {date.fromisoformat(r['day']): r for r in [*unchanged, *published]}
    return [by_day[d] for d in days], result
