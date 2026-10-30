"""Bounded expanded-CNRFC production pilot, without changing operational CNRFC."""
# Model helper APIs use naive datetimes representing UTC, not local time.
# ruff: noqa: DTZ001, DTZ007
import argparse
import fcntl
import json
import multiprocessing
import os
import shutil
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from backfill_cnrfc_forcing import discover, process
from netCDF4 import Dataset
from prepare_cnrfc_hu18_test import DOMAIN, approved, digest, identity
from run_cnrfc_production import run
from subset_nwm_restart import subset_pair
from test_cnrfc_hu18_model import integration

from hydro_ops.wrf_hydro.production import restart_names, write_json

CAMPAIGN = 'hu18_pilot_v1'


def selected(path, start=None, stop=None):
    """Include two summary years and the extra hourly end-boundary day."""
    stamp = path.name.split('.')[0]
    if start is not None:
        if path.name.endswith('.monthly'):
            # Only complete calendar months within the requested simulation window.
            month = datetime.strptime(stamp, '%Y%m')
            next_month = datetime(month.year + (month.month == 12), month.month % 12 + 1, 1)
            return start <= month and next_month <= stop
        day = datetime.strptime(stamp, '%Y%m%d')
        return start <= day <= stop if path.name.endswith('.LDASIN_DOMAIN1') else start <= day < stop
    if path.name.endswith('.monthly'):
        return '197901' <= stamp <= '198012'
    end = '19810101' if path.name.endswith('.LDASIN_DOMAIN1') else '19801231'
    return '19790101' <= stamp <= end


def required_hours(start=None, stop=None):
    day = start or datetime(1979, 1, 2)
    while day <= (stop or datetime(1981, 1, 1)):
        yield Path(day.strftime('retro/hourly/%Y/%m/%Y%m%d.LDASIN_DOMAIN1'))
        day += timedelta(days=1)


def check_parameters(domain):
    report = json.loads((domain / 'parameter_acceptance.json').read_text())
    if report.get('status') != 'passed':
        raise ValueError('Parameter acceptance failed')
    for name, item in report['files'].items():
        # Match the pinned extraction manifest before using shared parameters.
        path = domain / 'parameters' / name
        if digest(path) != item['sha256']:
            raise ValueError(f'Parameter checksum changed: {path}')


def backfill(root, domain, scratch, status, workers, start=None, stop=None):
    parent = root / 'forcing/outputs/conus'
    output = root / 'forcing/outputs' / DOMAIN
    paths = [p for p in discover(parent, ('retro',)) if selected(p, start, stop)]
    available = {p.relative_to(parent) for p in paths}
    missing = [str(p) for p in required_hours(start, stop) if p not in available]
    if missing:
        raise ValueError(f'Missing required CONUS hourly dates: {missing}')
    mask = domain / 'masks/cnrfc_hu18_masks.nc'
    mask_hash = digest(mask)
    output.mkdir(parents=True, exist_ok=True)
    with (output / '.subset-pilot.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        tasks = [(p, output / p.relative_to(parent), mask, mask_hash, scratch, identity(p))
                 for p in paths]
        report = {'status': 'running', 'domain': DOMAIN, 'mask_sha256': mask_hash,
                  'job_id': os.environ['SLURM_JOB_ID'], 'total': len(tasks),
                  'published': 0, 'skipped': 0, 'failed': 0,
                  'required_hourly_days': len(list(required_hours(start, stop))),
                  'start': str(start), 'stop': str(stop),
                  'summary_policy': 'Subset available summaries in selected interval; no new reductions',
                  'counts': {f: sum(f in p.relative_to(parent).parts for p in paths)
                             for f in ('hourly', 'daily', 'monthly')}}
        write_json(status / 'plan.json', report | {'sources': [str(p) for p in paths]})
        with (status / 'results.jsonl').open('a', buffering=1) as log, \
                ProcessPoolExecutor(max_workers=workers,
                                    mp_context=multiprocessing.get_context('spawn')) as pool:
            for result in pool.map(process, tasks, chunksize=1):
                log.write(json.dumps(result) + '\n')
                report[result['status']] += 1
                write_json(status / 'status.json', report)
        report['status'] = 'failed' if report['failed'] else 'passed'
        write_json(status / 'status.json', report)
        if report['failed']:
            raise RuntimeError('Forcing subset failures; model gate remains blocked')


def warm_gate(root, domain, scratch, status, *, domain_name=DOMAIN, campaign=CAMPAIGN,
              mask_name='cnrfc_hu18_masks.nc'):
    check_parameters(domain)
    parent = root / 'nwm/runs/conus/retro/production_1979_v1/initialization-preserved-counter'
    provenance = json.loads((parent / 'initialization.json').read_text())
    if not (provenance.get('physical_fields_verified_unchanged')
            and provenance.get('history_counter_preserved')):
        raise ValueError('CONUS spin-up initialization provenance is incomplete')
    initial = root / 'nwm/runs' / domain_name / 'retro' / campaign / 'initialization'
    if initial.exists():
        raise FileExistsError(f'Inspect existing initialization before retry: {initial}')
    start, end = datetime(1979, 1, 2), datetime(1979, 1, 5)
    names = restart_names(start)
    subset_pair(parent / names[0], parent / names[1],
                root / 'nwm/static/operational/nwm.v3.1.6/domain/RouteLink_CONUS.nc',
                domain / 'parameters', scratch / 'initialization')
    initial.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(scratch / 'initialization', initial)
    shutil.copy2(parent / 'initialization.json', initial / 'parent_initialization.json')
    forcing = root / 'forcing/outputs' / domain_name / 'retro/hourly'
    result = root / 'nwm/outputs' / domain_name / 'tests' / ('spinup-warm-' + os.environ['SLURM_JOB_ID'])
    continuous = integration(root, domain, forcing, start, end,
                             result / 'continuous', scratch / 'continuous', initial)
    resumed = integration(root, domain, forcing, end - timedelta(days=1), end,
                          result / 'resumed', scratch / 'resumed',
                          result / 'continuous/restarts/1979/01')
    differences = {}
    for name in restart_names(end):
        with Dataset(result / 'continuous/restarts/1979/01' / name) as a, \
                Dataset(result / 'resumed/restarts/1979/01' / name) as b:
            fields = ('SMC', 'SH2O', 'SOIL_T', 'SNEQV') if name.startswith('RESTART') else (
                'hlink', 'qlink1', 'qlink2', 'z_gwsubbas')
            for field in fields:
                x, y = np.ma.asarray(a[field][:]), np.ma.asarray(b[field][:])
                np.testing.assert_array_equal(np.ma.getmaskarray(x), np.ma.getmaskarray(y))
                np.testing.assert_allclose(x.compressed(), y.compressed(), rtol=1e-5, atol=1e-6)
                differences[field] = float(np.ma.max(np.ma.abs(x - y)))
    write_json(status / 'acceptance.json', {
        'status': 'passed', 'domain': domain_name, 'spinup_warm_start_verified': True,
        'continuous': continuous, 'resumed': resumed, 'max_abs_differences': differences,
        'initialization': initial, 'mask_sha256': digest(domain / 'masks' / mask_name),
        'initial_restart_sha256': {name: digest(initial / name) for name in names},
        'operational_cutover': False, 'scientific_validation': False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('forcing', 'gate', *map(str, range(1979, 2027))))
    parser.add_argument('--year', type=int, help='Bound forcing backfill to one continuation year')
    parser.add_argument('--continuation-plan', type=Path)
    parser.add_argument('--gate', type=Path)
    parser.add_argument('--workers', type=int, default=16)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error('--workers must be positive')
    root = Path(__file__).resolve().parents[1]
    domain = approved(root)
    start = stop = continuation_end = None
    if args.continuation_plan:
        if args.stage == 'gate' or (args.stage == 'forcing' and args.year is None):
            parser.error('Continuation requires an annual model stage or forcing --year')
        plan = json.loads(args.continuation_plan.read_text())
        if plan.get('domain') != DOMAIN or plan.get('authorized') is not True:
            raise ValueError('Invalid continuation authorization')
        continuation_end = datetime.fromisoformat(plan['end_exclusive'])
        year = args.year if args.stage == 'forcing' else int(args.stage)
        start = datetime(year, 1, 1)
        stop = min(datetime(year + 1, 1, 1), continuation_end)
        if not datetime.fromisoformat(plan['start']) <= start < stop:
            raise ValueError('Year outside continuation window')
    elif args.year is not None or (args.stage.isdigit() and int(args.stage) > 1980):
        parser.error('Continuation requires --continuation-plan')
    scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{os.environ["SLURM_JOB_ID"]}')
    scratch.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(scratch).free < 20 * 1024**3:
        raise RuntimeError('Insufficient scratch')
    status = root / 'nwm/status' / DOMAIN / CAMPAIGN / ('job-' + os.environ['SLURM_JOB_ID'])
    status.mkdir(parents=True, exist_ok=False)
    if args.stage == 'forcing':
        backfill(root, domain, scratch, status, args.workers, start, stop)
    elif args.stage == 'gate':
        warm_gate(root, domain, scratch, status)
    else:
        check_parameters(domain)
        if args.gate is None:
            parser.error('Annual runs require --gate')
        gate = json.loads(args.gate.read_text())
        if gate.get('mask_sha256') != digest(domain / 'masks/cnrfc_hu18_masks.nc'):
            raise ValueError('Warm-start gate mask mismatch')
        if args.stage == '1979':
            initial = root / 'nwm/runs' / DOMAIN / 'retro' / CAMPAIGN / 'initialization'
            for name in restart_names(datetime(1979, 1, 2)):
                if digest(initial / name) != gate['initial_restart_sha256'][name]:
                    raise ValueError('Gated initial restart changed')
        run(root, int(args.stage), campaign=CAMPAIGN, archive_gate=args.gate, domain=DOMAIN,
            end_date=stop.strftime('%Y-%m-%d') if stop else None,
            continuation_end=continuation_end)


if __name__ == '__main__':
    main()
