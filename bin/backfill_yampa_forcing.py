"""Extract Yampa products after an explicitly accepted UCRB backfill."""
import argparse
import fcntl
import json
import multiprocessing
import os
import shutil
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from backfill_b120_forcing import checked_process
from backfill_cnrfc_forcing import digest, discover, identity
from backfill_ucrb_forcing import hourly_gaps
from netCDF4 import Dataset
from subset_nwm_forcing import subset

from hydro_ops.wrf_hydro.production import write_json


def check_parent(report):
    if (report.get('status') != 'passed' or report.get('domain') != 'ucrb'
            or report.get('failed') != 0 or not report.get('total')
            or report.get('published', 0) + report.get('skipped', 0) != report['total']):
        raise ValueError('UCRB parent backfill is not completely accepted')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent-report', type=Path, required=True)
    args = parser.parse_args()
    check_parent(json.loads(args.parent_report.read_text()))
    root = Path(__file__).resolve().parents[1]
    domain = root / 'nwm/static/domains/yampa'
    mask = domain / 'masks/yampa_masks.nc'
    accepted = json.loads((domain / 'manifest.json').read_text())
    mask_hash = digest(mask)
    if (accepted.get('status') != 'passed' or accepted.get('domain') != 'yampa'
            or accepted.get('approved_for_production') is not True
            or accepted.get('mask_sha256') != mask_hash
            or accepted.get('boundary_sha256') != digest(domain / 'boundaries/yampa_boundaries.gpkg')):
        raise ValueError('Yampa boundary/masks unapproved or changed')
    parent, output = root / 'forcing/outputs/ucrb', root / 'forcing/outputs/yampa'
    job = os.environ['SLURM_JOB_ID']
    scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{job}/yampa-forcing')
    scratch.mkdir(parents=True, exist_ok=False)
    if shutil.disk_usage(scratch).free < 20 * 1024**3:
        raise RuntimeError('Insufficient scratch')
    state = root / 'forcing/status/yampa' / ('backfill-' + job)
    state.mkdir(parents=True, exist_ok=False)
    output.mkdir(parents=True, exist_ok=True)
    with (output / '.subset.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        paths = discover(parent)
        # Require every file in the accepted parent campaign, not just no interior gaps.
        parent_plan = json.loads(args.parent_report.with_name('plan.json').read_text())
        expected = {parent / Path(p['path']).relative_to(root / 'forcing/outputs/conus')
                    for p in parent_plan['sources']}
        if not expected.issubset(set(paths)):
            raise ValueError('Accepted parent campaign files are missing')
        gaps = {s: hourly_gaps(paths, parent, s) for s in ('retro', 'nrt')}
        if any(gaps.values()):
            raise ValueError(f'Interior source gaps: {gaps}')
        report = {'status': 'running', 'job_id': job, 'domain': 'yampa', 'source_domain': 'ucrb',
                  'workers': 16, 'total': len(paths), 'published': 0, 'skipped': 0, 'failed': 0,
                  'parent_report': str(args.parent_report.resolve()), 'hourly_source_gaps': gaps}
        tasks = [(p, output / p.relative_to(parent), mask, mask_hash, scratch, identity(p)) for p in paths]
        write_json(state / 'plan.json', report | {'mask_sha256': mask_hash,
            'sources': [{'path': str(t[0]), 'identity': t[-1]} for t in tasks]})
        write_json(state / 'status.json', report)
        samples = []
        for stream in ('retro', 'nrt'):
            for frequency in ('hourly', 'daily', 'monthly'):
                matches = [t for t in tasks if t[0].relative_to(parent).parts[:2] == (stream, frequency)]
                if not matches:
                    raise ValueError(f'Missing source product group {stream}/{frequency}')
                task = matches[-1]
                result = checked_process(task)
                if result['status'] == 'failed':
                    raise RuntimeError(str(result))
                source, target = task[:2]
                conus = root / 'forcing/outputs/conus' / source.relative_to(parent)
                before = identity(conus)
                direct = scratch / f'{stream}-{frequency}-direct.nc'
                audit = subset(conus, direct, mask)
                with Dataset(target) as x, Dataset(direct) as y:
                    for name in audit['validated_fields']:
                        np.testing.assert_array_equal(np.ma.getmaskarray(x[name][:]), np.ma.getmaskarray(y[name][:]))
                        np.testing.assert_array_equal(x[name][:].compressed(), y[name][:].compressed())
                if identity(conus) != before or identity(source) != task[-1]:
                    raise ValueError('Sample source changed during validation')
                samples.append({'stream': stream, 'frequency': frequency, 'status': 'passed'})
        write_json(state / 'sample_acceptance.json', {'status': 'passed', 'samples': samples})
        with (state / 'results.jsonl').open('a', buffering=1) as log, ProcessPoolExecutor(
                max_workers=16, mp_context=multiprocessing.get_context('spawn')) as pool:
            for result in pool.map(checked_process, tasks, chunksize=1):
                log.write(json.dumps(result) + '\n')
                report[result['status']] += 1
                write_json(state / 'status.json', report)
        report['status'] = 'failed' if report['failed'] else 'passed'
        write_json(state / 'status.json', report)
        if report['failed']:
            raise RuntimeError('Subset failures; see results.jsonl')


if __name__ == '__main__':
    try:
        main()
    except BaseException as error:
        state = (Path(__file__).resolve().parents[1] / 'forcing/status/yampa'
                 / ('backfill-' + os.environ['SLURM_JOB_ID']) / 'status.json')
        report = json.loads(state.read_text()) if state.exists() else {}
        write_json(state, report | {'status': 'failed', 'error': str(error)})
        raise
