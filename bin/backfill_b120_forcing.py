"""Audited B120 hourly/daily/monthly crops from the operational CNRFC archive."""
import fcntl
import json
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from backfill_cnrfc_forcing import digest, discover, identity, process_retry
from netCDF4 import Dataset
from subset_nwm_forcing import subset

from hydro_ops.wrf_hydro.production import write_json


def checked_process(task):
    source = task[0]
    try:
        audit = json.loads(source.with_name(source.name + '.subset.json').read_text())
        if audit.get('status') != 'passed' or audit.get('output_identity') != identity(source):
            raise ValueError('Missing/stale accepted CNRFC source receipt')
        return process_retry(task)
    except (OSError, ValueError, RuntimeError) as error:
        return {'status': 'failed', 'source': str(source), 'error': str(error)}


def main():
    root = Path(__file__).resolve().parents[1]
    parent, output = root / 'forcing/outputs/cnrfc', root / 'forcing/outputs/b120'
    mask = root / 'nwm/static/domains/b120/masks/b120_masks.nc'
    accepted = json.loads((root / 'nwm/static/domains/b120/manifest.json').read_text())
    if accepted.get('status') != 'passed' or accepted.get('mask_sha256') != digest(mask):
        raise ValueError('B120 masks are not accepted or changed')
    job = os.environ['SLURM_JOB_ID']
    scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{job}/b120-forcing')
    scratch.mkdir(parents=True, exist_ok=False)
    state = root / 'forcing/status/b120' / ('backfill-' + job)
    state.mkdir(parents=True, exist_ok=False)
    output.mkdir(parents=True, exist_ok=True)
    with (output / '.subset.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        paths = discover(parent)
        if not paths:
            raise ValueError('CNRFC source archive is empty')
        missing = {}
        for stream in ('retro', 'nrt'):
            days = {date.fromisoformat(p.name[:4] + '-' + p.name[4:6] + '-' + p.name[6:8])
                    for p in paths if p.relative_to(parent).parts[:2] == (stream, 'hourly')}
            if not days:
                raise ValueError(f'No {stream} hourly source files')
            gaps, day = [], min(days)
            while day <= max(days):
                if day not in days:
                    gaps.append(day.isoformat())
                day += timedelta(days=1)
            missing[stream] = gaps
            if set(gaps) - {'2026-03-03', '2026-03-04'}:
                raise ValueError(f'Unexpected {stream} source gaps: {gaps}')
        report = {'status': 'running', 'job_id': job, 'source_domain': 'cnrfc',
                  'domain': 'b120', 'workers': 16, 'total': len(paths),
                  'published': 0, 'skipped': 0, 'failed': 0, 'deferred_source_gaps': missing,
                  'counts': {f'{s}/{f}': sum(p.relative_to(parent).parts[:2] == (s, f) for p in paths)
                             for s in ('retro', 'nrt') for f in ('hourly', 'daily', 'monthly')}}
        write_json(state / 'status.json', report)
        mask_hash = digest(mask)
        tasks = [(p, output / p.relative_to(parent), mask, mask_hash, scratch, identity(p)) for p in paths]
        write_json(state / 'plan.json', report | {'mask_sha256': mask_hash, 'sources': [str(p) for p in paths]})
        # Before bulk publication, verify chained crops against independent CONUS crops.
        samples = []
        for stream in ('retro', 'nrt'):
            for frequency in ('hourly', 'daily', 'monthly'):
                matches = [p for p in paths if p.relative_to(parent).parts[:2] == (stream, frequency)]
                if not matches:
                    continue
                source = matches[-1]
                before = identity(source)
                label = stream + '-' + frequency
                chained, direct = scratch / (label + '-chained.nc'), scratch / (label + '-direct.nc')
                a = subset(source, chained, mask)
                conus = root / 'forcing/outputs/conus' / source.relative_to(parent)
                parent_before = identity(conus)
                subset(conus, direct, mask)
                with Dataset(chained) as x, Dataset(direct) as y:
                    for name in a['validated_fields']:
                        np.testing.assert_array_equal(np.ma.getmaskarray(x[name][:]), np.ma.getmaskarray(y[name][:]))
                        np.testing.assert_array_equal(x[name][:].compressed(), y[name][:].compressed())
                if identity(source) != before or identity(conus) != parent_before:
                    raise ValueError('Sample parent changed during validation; retry')
                samples.append({'stream': stream, 'frequency': frequency, 'source': str(source), 'status': 'passed'})
        write_json(state / 'chained_crop_acceptance.json', {'status': 'passed', 'samples': samples})
        with (state / 'results.jsonl').open('a', buffering=1) as log, ProcessPoolExecutor(
                max_workers=16, mp_context=multiprocessing.get_context('spawn')) as pool:
            for result in pool.map(checked_process, tasks, chunksize=1):
                log.write(json.dumps(result) + '\n')
                report[result['status']] += 1
                write_json(state / 'status.json', report)
        report['status'] = 'failed' if report['failed'] else 'passed'
        write_json(state / 'status.json', report)
        if report['failed']:
            raise RuntimeError('Subset failures; see per-file results')


if __name__ == '__main__':
    try:
        main()
    except BaseException as error:
        state = (Path(__file__).resolve().parents[1] / 'forcing/status/b120'
                 / ('backfill-' + os.environ['SLURM_JOB_ID']) / 'status.json')
        report = json.loads(state.read_text()) if state.exists() else {}
        write_json(state, report | {'status': 'failed', 'error': str(error)})
        raise
