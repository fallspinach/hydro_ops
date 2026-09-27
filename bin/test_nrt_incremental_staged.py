"""Isolated incremental operational acceptance for the configurable staged path."""
import json
import os
import shutil
import time
from datetime import UTC, date, datetime
from pathlib import Path

from benchmark_nrt_extension import clone, compare, compare_inputs, receipt

from hydro_ops.forcing import nrt_cycle as cycle
from hydro_ops.forcing.gfs_publication import _atomic_json
from hydro_ops.forcing.window_cache import checksum


def clone_cache(source, target):
    shutil.copytree(source, target)
    for entry in target.iterdir():
        if not entry.is_dir() or not (entry / 'receipt.json').is_file():
            continue
        record = cycle.read_json(entry / 'receipt.json')
        if checksum(entry / 'window.nc') != record['sha256']:
            raise ValueError('Copied cache checksum mismatch')
        record['identity'] = cycle.identity(entry / 'window.nc')
        _atomic_json(entry / 'receipt.json', record)


def run(root, arm, work, as_of, pipeline):
    os.environ['HYDRO_OPS_NRT_WINDOW_CACHE'] = str(arm / 'cache')
    return cycle.run_cycle(root, work, date(2026, 9, 18), date(2026, 9, 24), as_of,
                           baseline_root=arm / 'baseline', output_root=arm / 'nrt',
                           state_root=arm / 'status', pipeline=pipeline,
                           requested_at=datetime.now(UTC).isoformat())


def clone_arm(source, target):
    for kind in ('baseline', 'nrt'):
        for path in sorted((source / kind).glob('*/*/*.LDASIN_DOMAIN1')):
            clone(path, target / kind / path.relative_to(source / kind))


def main():
    root = Path(__file__).resolve().parents[1]
    job = os.environ['SLURM_JOB_ID']
    campaign = root / f'forcing/work/nrt-incremental-staged/job_{job}'
    campaign.mkdir(parents=True, exist_ok=False)
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{job}")
    source = root / 'forcing/work/nrt-staged-revision/job_4655305'
    if cycle.read_json(source / 'acceptance.json').get('exact_values_verified') is not True:
        raise ValueError('Validated source benchmark required')
    report = {'status': 'running', 'job': job, 'scenario': 'one missing baseline plus dependent revisions', 'trials': {}}
    output = campaign / 'acceptance.json'
    _atomic_json(output, report)
    as_of = datetime.now(UTC)
    try:
        seed = campaign / 'seed'
        clone_arm(source / 'candidate', seed)
        # Force private final publications to prime persistent window reuse;
        # production files and the previously validated source remain read-only.
        for path in (seed / 'nrt').glob('*/*/*.nrt-receipt.json'):
            record = cycle.read_json(path)
            record['input_fingerprint'] = 'private-cache-priming'
            _atomic_json(path, record)
        report['seed'] = run(root, seed, scratch / 'seed', as_of, 'serial')
        _atomic_json(output, report)
        if report['seed']['status'] != 'passed':
            raise ValueError('Private seed failed')
        for name, pipeline in (('candidate', 'staged_v1'), ('reference', 'serial')):
            arm = campaign / name
            clone_arm(seed, arm)
            clone_cache(seed / 'cache', arm / 'cache')
            withheld = arm / 'withheld'
            withheld.mkdir()
            missing = cycle.day_path(arm / 'baseline', date(2026, 9, 23))
            missing.resolve().relative_to(arm.resolve())
            missing.rename(withheld / missing.name)
            stale = receipt(cycle.day_path(arm / 'nrt', date(2026, 9, 23)))
            record = cycle.read_json(stale)
            record['input_fingerprint'] = 'private-incremental-refresh'
            _atomic_json(stale, record)
            started = time.monotonic()
            trial = run(root, arm, scratch / name, as_of, pipeline)
            report['trials'][name] = {'seconds': time.monotonic() - started, 'cycle': trial}
            _atomic_json(output, report)
            if trial['status'] != 'passed':
                raise ValueError(f'{name} failed')
        started = time.monotonic()
        a, b = campaign / 'reference', campaign / 'candidate'
        for kind in ('baseline', 'nrt'):
            paths = sorted((a / kind).glob('*/*/*.LDASIN_DOMAIN1'))
            if [p.relative_to(a / kind) for p in paths] != [p.relative_to(b / kind) for p in sorted((b / kind).glob('*/*/*.LDASIN_DOMAIN1'))]:
                raise ValueError('Trial coverage differs')
            for left in paths:
                right = b / kind / left.relative_to(a / kind)
                ar, br = cycle.read_json(receipt(left)), cycle.read_json(receipt(right))
                if kind == 'baseline':
                    if ar['input_fingerprint'] != br['input_fingerprint']:
                        raise ValueError('Baseline source fingerprints differ')
                else:
                    compare_inputs(br, ar)
                compare(left, right)
        report['comparison_seconds'] = time.monotonic() - started
        stages = report['trials']['candidate']['cycle']['staged_timings']
        if sum(r['status'] == 'published' for r in stages['baseline_days']) != 1:
            raise ValueError('Expected exactly one rebuilt baseline')
        hits = sum(t['stage'] == 'persistent_window_hit' for r in stages['windows'] for t in r['stage_timings'])
        if not hits:
            raise ValueError('Incremental test exercised no persistent window reuse')
        frozen = {str(p): cycle.identity(p) for kind in ('baseline', 'nrt')
                  for p in (b / kind).glob('*/*/*.LDASIN_DOMAIN1')}
        repeat = run(root, b, scratch / 'repeat', as_of, 'staged_v1')
        report['repeat'] = repeat
        if repeat['status'] != 'passed' or any(d['status'] != 'unchanged' for d in repeat['days']):
            raise ValueError('Repeat was not unchanged')
        if repeat['staged_timings']['windows'] or any(cycle.identity(Path(p)) != v for p, v in frozen.items()):
            raise ValueError('Repeat rebuilt windows or changed outputs')
        seconds = report['trials']['candidate']['seconds']
        report.update(status='passed', exact_values_verified=True, persistent_window_hits=hits,
                      candidate_under_hour=seconds < 3600,
                      speedup=report['trials']['reference']['seconds'] / seconds)
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        report['finished_utc'] = datetime.now(UTC).isoformat()
        _atomic_json(output, report)
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
