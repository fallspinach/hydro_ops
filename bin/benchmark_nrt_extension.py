"""Replay a one-day NRT extension using isolated retained baseline copies."""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.config import load_settings
from hydro_ops.forcing import nrt_cycle as cycle
from hydro_ops.forcing.gfs_publication import _atomic_json


def receipt(path):
    return path.with_name(path.name + '.nrt-receipt.json')


def clone(source, destination):
    record = cycle.read_json(receipt(source))
    if record.get('status') != 'passed' or record.get('published_identity') != cycle.identity(source):
        raise ValueError(f'Stale source acceptance: {source}')
    checksum = cycle.transfer(source, destination)
    if checksum != record['sha256']:
        raise ValueError(f'Source checksum mismatch: {source}')
    record['published_identity'] = cycle.identity(destination)
    _atomic_json(receipt(destination), record)


def compare(left, right):
    with Dataset(left) as a, Dataset(right) as b:
        assert set(a.variables) == set(b.variables)
        assert {k: len(v) for k, v in a.dimensions.items()} == {k: len(v) for k, v in b.dimensions.items()}
        for name, var in a.variables.items():
            other = b[name]
            assert (var.dtype, var.dimensions, var.shape) == (other.dtype, other.dimensions, other.shape)
            assert set(var.ncattrs()) == set(other.ncattrs())
            for attr in var.ncattrs():
                np.testing.assert_array_equal(var.getncattr(attr), other.getncattr(attr))
            var.set_auto_maskandscale(False)
            other.set_auto_maskandscale(False)
            steps = range(var.shape[0]) if var.dimensions and var.dimensions[0] == 'time' else [Ellipsis]
            for step in steps:
                np.testing.assert_array_equal(var[step], other[step], err_msg=f'{name}/{step}')
        for attr in ('forcing_stream', 'archive_granularity', 'forcing_domain_policy',
                     'prism_reconciliation_accepted', 'prism_precipitation_revisions'):
            assert (attr in a.ncattrs()) == (attr in b.ncattrs()), attr
            if attr in a.ncattrs():
                assert a.getncattr(attr) == b.getncattr(attr), attr


def compare_inputs(new, reference):
    # File SHA tracks a particular artifact, not scientific equivalence across runs.
    # Baseline source fingerprints must still match; fields are compared separately.
    a = {k: v for k, v in new['inputs'].items() if k != 'baseline_sha256'}
    b = {k: v for k, v in reference['inputs'].items() if k != 'baseline_sha256'}
    if a != b:
        raise ValueError('Source inputs differ from cold reference')


def validate_existing(root, campaign):
    """Read-only comparison and guarded repeat; preserve the original failed report."""
    plan = cycle.read_json(campaign / 'submission.json')
    original = cycle.read_json(campaign / 'acceptance.json')
    if original.get('extension_cycle', {}).get('status') != 'passed':
        raise ValueError('No successful extension to validate')
    source, target = Path(plan['source']), date.fromisoformat(plan['target'])
    result = {'status': 'running', 'job': os.environ['SLURM_JOB_ID'],
              'original_job': original['job'], 'extension_cycle': original['extension_cycle']}
    output = campaign / 'validation.json'
    if output.exists():
        raise FileExistsError(output)
    _atomic_json(output, result)
    snapshot = {str(p): cycle.identity(p) for base in (source, campaign)
                for folder in ('baseline', 'nrt') for p in (base / folder).glob('*/*/*.LDASIN_DOMAIN1')}
    try:
        started = time.monotonic()
        for offset in (-2, -1, 0, 1):
            day = target + timedelta(days=offset)
            a, b = (cycle.day_path(base / 'baseline', day) for base in (source, campaign))
            ar, br = cycle.read_json(receipt(a)), cycle.read_json(receipt(b))
            if ar['input_fingerprint'] != br['input_fingerprint']:
                raise ValueError(f'Baseline sources differ: {day}')
            for path, record in ((a, ar), (b, br)):
                if record['published_identity'] != cycle.identity(path):
                    raise ValueError(f'Stale baseline receipt: {path}')
            compare(a, b)
        a, b = (cycle.day_path(base / 'nrt', target) for base in (source, campaign))
        compare_inputs(cycle.read_json(receipt(b)), cycle.read_json(receipt(a)))
        compare(a, b)
        result.update(comparison_seconds=time.monotonic() - started, cold_reference_comparison='passed')
        as_of = datetime.fromisoformat(original['extension_cycle']['as_of'])
        work = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}") / 'validation'
        def forbidden(*args, **kwargs):
            raise RuntimeError('Validation-only repeat requires rebuilding; refusing')
        build, launch = cycle.produce_complete_day, subprocess.run
        cycle.produce_complete_day = subprocess.run = forbidden
        try:
            repeat = cycle.run_cycle(root, work, target - timedelta(days=1), target, as_of,
                baseline_root=campaign / 'baseline', output_root=campaign / 'nrt',
                state_root=campaign / 'validation-status', requested_at=datetime.now(UTC).isoformat())
        finally:
            cycle.produce_complete_day, subprocess.run = build, launch
        result['repeat_cycle'] = repeat
        if repeat['status'] != 'passed' or any(d['status'] != 'unchanged' for d in repeat['days']):
            raise ValueError('Repeat was not unchanged')
        if any(cycle.identity(Path(p)) != value for p, value in snapshot.items()):
            raise ValueError('Existing output changed')
        result['status'] = 'passed'
    except Exception as error:
        result.update(status='failed', error=str(error))
        raise
    finally:
        _atomic_json(output, result)
        print(json.dumps(result), flush=True)


def run(root, campaign):
    plan = json.loads((campaign / 'submission.json').read_text())
    source, target = Path(plan['source']), date.fromisoformat(plan['target'])
    acceptance = cycle.read_json(source / 'acceptance.json')
    if acceptance.get('status') != 'passed':
        raise ValueError('Source campaign did not pass')
    cycle.require_operational_gfs(root)
    as_of = datetime.fromisoformat(acceptance['first_cycle']['as_of'])
    work = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}") / 'extension'
    work.mkdir(parents=True, exist_ok=False)
    options = {'baseline_root': campaign / 'baseline', 'output_root': campaign / 'nrt',
                   'state_root': campaign / 'status'}
    report = {'status': 'running', 'job': os.environ['SLURM_JOB_ID'], 'target': str(target),
                  'scope': 'Historical one-day extension replay; original as-of; existing source cache; fresh scratch'}
    output = campaign / 'acceptance.json'
    _atomic_json(output, report)
    originals = {}
    try:
        # Do not confuse a source revision with the cost of a pure extension.
        for offset in (-2, -1, 0, 1):
            reference_day = cycle.day_path(source / 'baseline', target + timedelta(days=offset))
            record = cycle.read_json(receipt(reference_day))
            if not record or record['inputs']['configuration'] != cycle.baseline_configuration(cycle.configuration(root)):
                raise ValueError('Baseline configuration changed since reference')
            for item in record['inputs']['files'] + record['inputs']['assets']:
                if cycle.identity(Path(item['path'])) != item:
                    raise ValueError(f"Reference input changed: {item['path']}")
        for item in cycle.read_json(receipt(cycle.day_path(source / 'nrt', target)))['inputs']['prism']:
            if cycle.identity(Path(item['path'])) != item:
                raise ValueError(f"Reference PRISM changed: {item['path']}")
        started = time.monotonic()
        for offset in (-2, -1, 0):
            day = target + timedelta(days=offset)
            original = cycle.day_path(source / 'baseline', day)
            originals[str(original)] = cycle.identity(original)
            clone(original, cycle.day_path(options['baseline_root'], day))
        previous = target - timedelta(days=1)
        original = cycle.day_path(source / 'nrt', previous)
        originals[str(original)] = cycle.identity(original)
        clone(original, cycle.day_path(options['output_root'], previous))
        reference = cycle.day_path(source / 'nrt', target)
        originals[str(reference)] = cycle.identity(reference)
        before = {str(p): cycle.identity(p) for folder in ('baseline', 'nrt')
                  for p in (campaign / folder).glob('*/*/*.LDASIN_DOMAIN1')}
        report['setup_seconds'] = time.monotonic() - started
        if plan.get('warm_window_cache'):
            # Previous cycle populates the shared window; use separate output and
            # scratch so it cannot pre-publish the extension or leak scratch reuse.
            os.environ['HYDRO_OPS_NRT_WINDOW_CACHE'] = str(campaign / 'nrt/.prism-window-cache')
            seed = cycle.run_cycle(root, work.parent / 'seed', previous, previous, as_of,
                baseline_root=options['baseline_root'], output_root=campaign / 'seed',
                state_root=campaign / 'seed-status', requested_at=datetime.now(UTC).isoformat())
            report['cache_seed_cycle'] = seed
            _atomic_json(output, report)
            if seed['status'] != 'passed':
                raise ValueError('Previous-cycle cache seed failed')
        events = []
        original_method = cycle.RecentNrt.baseline_day

        def timed_baseline(engine, day):
            path = cycle.day_path(engine.baseline, day)
            old, start = cycle.identity(path), time.monotonic()
            result = original_method(engine, day)
            events.append({'day': str(day), 'seconds': time.monotonic() - start,
                               'rebuilt': old != cycle.identity(path)})
            report['baseline_calls'] = events
            _atomic_json(output, report)
            return result

        cycle.RecentNrt.baseline_day = timed_baseline
        try:
            first = cycle.run_cycle(root, work, previous, target, as_of,
                                    requested_at=datetime.now(UTC).isoformat(), **options)
        finally:
            cycle.RecentNrt.baseline_day = original_method
        report['extension_cycle'] = first
        if first['status'] != 'passed':
            raise ValueError('Extension failed')
        if [(d['day'], d['status']) for d in first['days']] != [(str(previous), 'unchanged'), (str(target), 'published')]:
            raise ValueError('Not a pure extension: old sources changed or replacement backlog exists')
        rebuilt = sorted({e['day'] for e in events if e['rebuilt']})
        report['baseline_days_rebuilt'] = rebuilt
        if rebuilt != [str(target + timedelta(days=1))]:
            raise ValueError('Expected exactly one new support baseline')
        support = cycle.read_json(receipt(cycle.day_path(options['baseline_root'], target + timedelta(days=1))))
        report['baseline_stage_timings'] = support.get('baseline_stage_timings', [])
        if plan.get('require_full_optimizations'):
            stages = {s['stage']: s for s in report['baseline_stage_timings']}
            if (stages['complete_day']['assembly_workers'] != 8
                    or stages['complete_day']['precipitation_remap_workers'] != 4
                    or stages['native_donor_repair']['workers'] != 4
                    or not stages['gfs_publication_and_audit']['sparse_writes']):
                raise ValueError('Expected all adopted baseline optimizations')
        if any(cycle.identity(Path(p)) != value for p, value in before.items()):
            raise ValueError('Retained files changed')
        final = cycle.day_path(options['output_root'], target)
        new, ref = cycle.read_json(receipt(final)), cycle.read_json(receipt(reference))
        compare_inputs(new, ref)
        report['publication'] = {k: new[k] for k in ('calendar_archive_writer', 'prism_window_archive_writers', 'gfs_hours', 'prism_constrained')}
        report['stage_timings'] = new.get('stage_timings', [])
        if plan.get('warm_window_cache'):
            hits = [s for s in report['stage_timings'] if s['stage'] == 'persistent_window_hit']
            if len(hits) != 1:
                raise ValueError('Expected exactly one cross-cycle window hit')
        started = time.monotonic()
        compare(cycle.day_path(source / 'baseline', target + timedelta(days=1)),
                cycle.day_path(options['baseline_root'], target + timedelta(days=1)))
        compare(reference, final)
        report['comparison_seconds'] = time.monotonic() - started
        report['cold_reference_comparison'] = 'passed'
        snapshot = {str(p): cycle.identity(p) for folder in ('baseline', 'nrt')
                    for p in (campaign / folder).glob('*/*/*.LDASIN_DOMAIN1')}
        repeat = cycle.run_cycle(root, work, previous, target, as_of,
                                 requested_at=datetime.now(UTC).isoformat(), **options)
        report['repeat_cycle'] = repeat
        if repeat['status'] != 'passed' or any(d['status'] != 'unchanged' for d in repeat['days']):
            raise ValueError('Repeat changed inputs')
        if any(cycle.identity(Path(p)) != value for p, value in snapshot.items()):
            raise ValueError('Repeat rewrote a file')
        if any(cycle.identity(Path(p)) != value for p, value in originals.items()):
            raise ValueError('Original campaign files changed')
        report.update(status='passed', extension_under_hour=first['latency']['worker_seconds'] < 3600)
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        _atomic_json(output, report)
        print(json.dumps(report), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--target', type=date.fromisoformat)
    parser.add_argument('--campaign', type=Path)
    parser.add_argument('--submit', action='store_true')
    parser.add_argument('--validate-existing', action='store_true')
    parser.add_argument('--warm-window-cache', action='store_true')
    parser.add_argument('--require-full-optimizations', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.campaign:
        args.campaign.resolve().relative_to(root / 'forcing/work')
        if args.validate_existing:
            return validate_existing(root, args.campaign.resolve())
        return run(root, args.campaign.resolve())
    if not args.submit or not args.source or not args.target:
        parser.error('Specify --submit --source --target, or --campaign')
    cycle.require_operational_gfs(root)
    settings = load_settings()
    campaign = root / f'forcing/work/nrt-extension-{datetime.now(UTC):%Y%m%dT%H%M%S}'
    campaign.mkdir(exist_ok=False)
    plan = {'source': str(args.source.resolve()), 'target': str(args.target), 'cpus': 64, 'scratch_mb': 240000}
    plan['warm_window_cache'] = args.warm_window_cache
    plan['require_full_optimizations'] = args.require_full_optimizations
    command = ['sbatch', '--parsable', f'--partition={settings.slurm_partition}',
               '--nodes=1', '--ntasks=1', '--cpus-per-task=64', '--tmp=240000', '--time=12:00:00',
               f'--job-name=nrt-one-day-extension-{args.target}', f'--chdir={root}',
               f'--output={root}/forcing/logs/nrt-extension-%j.out']
    if settings.slurm_account:
        command.append(f'--account={settings.slurm_account}')
    command += ['--wrap', f'{sys.executable} -u {Path(__file__).resolve()} --campaign {campaign}']
    env = {k: v for k, v in os.environ.items() if not k.startswith('HYDRO_OPS_')}
    env.update(OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1',
               PYTHONPATH=str(root / 'src'), PATH=str(Path(sys.executable).parent) + ':' + env['PATH'])
    plan['command'] = command
    _atomic_json(campaign / 'submission.json', plan)
    plan['job'] = subprocess.check_output(command, env=env, text=True).strip().split(';')[0]
    _atomic_json(campaign / 'submission.json', plan)
    print(json.dumps(dict(**plan, campaign=str(campaign)), indent=2))


if __name__ == '__main__':
    main()
