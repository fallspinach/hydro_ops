"""Audited CONUS-to-UCRB hourly/daily/monthly extraction, both forcing streams."""
import fcntl
import json
import multiprocessing
import os
import shutil
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from backfill_cnrfc_forcing import digest, discover, identity, process_retry

from hydro_ops.wrf_hydro.production import write_json


def approved(domain):
    record = json.loads((domain / 'manifest.json').read_text())
    if (record.get('status') != 'passed' or record.get('domain') != 'ucrb'
            or record.get('approved_for_production') is not True
            or record.get('mask_sha256') != digest(domain / 'masks/ucrb_masks.nc')
            or record.get('boundary_sha256') != digest(domain / 'boundaries/ucrb_boundaries.gpkg')):
        raise ValueError('UCRB boundary/masks are unapproved or changed')


def hourly_gaps(paths, parent, stream):
    days = {date(int(p.name[:4]), int(p.name[4:6]), int(p.name[6:8])) for p in paths
            if p.relative_to(parent).parts[:2] == (stream, 'hourly')}
    if not days:
        raise ValueError(f'No {stream} hourly sources')
    missing, day = [], min(days)
    while day <= max(days):
        if day not in days:
            missing.append(day.isoformat())
        day += timedelta(days=1)
    return missing


def main():
    root = Path(__file__).resolve().parents[1]
    domain = root / 'nwm/static/domains/ucrb'
    approved(domain)
    parent, output = root / 'forcing/outputs/conus', root / 'forcing/outputs/ucrb'
    mask = domain / 'masks/ucrb_masks.nc'
    mask_hash = digest(mask)
    job = os.environ['SLURM_JOB_ID']
    scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{job}/ucrb-forcing')
    scratch.mkdir(parents=True, exist_ok=False)
    if shutil.disk_usage(scratch).free < 20 * 1024**3:
        raise RuntimeError('Insufficient scratch')
    state = root / 'forcing/status/ucrb' / ('backfill-' + job)
    state.mkdir(parents=True, exist_ok=False)
    output.mkdir(parents=True, exist_ok=True)
    with (output / '.subset.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        paths = discover(parent)
        gaps = {s: hourly_gaps(paths, parent, s) for s in ('retro', 'nrt')}
        if any(gaps.values()):
            raise ValueError(f'Interior hourly source gaps: {gaps}')
        report = {'status': 'running', 'job_id': job, 'domain': 'ucrb', 'source_domain': 'conus',
                  'workers': 16, 'total': len(paths), 'published': 0, 'skipped': 0, 'failed': 0,
                  'hourly_source_gaps': gaps,
                  'counts': {f'{s}/{f}': sum(p.relative_to(parent).parts[:2] == (s, f) for p in paths)
                             for s in ('retro', 'nrt') for f in ('hourly', 'daily', 'monthly')}}
        write_json(state / 'status.json', report)
        tasks = [(p, output / p.relative_to(parent), mask, mask_hash, scratch, identity(p)) for p in paths]
        write_json(state / 'plan.json', report | {'mask_sha256': mask_hash,
            'sources': [{'path': str(t[0]), 'identity': t[-1]} for t in tasks]})
        # Gate bulk work on one audited sample from every stream/resolution.
        samples = []
        for stream in ('retro', 'nrt'):
            for frequency in ('hourly', 'daily', 'monthly'):
                matches = [t for t in tasks if t[0].relative_to(parent).parts[:2] == (stream, frequency)]
                if not matches:
                    raise ValueError(f'Missing source product group: {stream}/{frequency}')
                result = process_retry(matches[-1])
                samples.append(result | {'stream': stream, 'frequency': frequency})
                write_json(state / 'sample_acceptance.json', {'status': 'running', 'samples': samples})
                if result['status'] == 'failed':
                    raise RuntimeError(f'Sample crop failed: {result}')
        write_json(state / 'sample_acceptance.json', {'status': 'passed', 'samples': samples})
        with (state / 'results.jsonl').open('a', buffering=1) as log, ProcessPoolExecutor(
                max_workers=16, mp_context=multiprocessing.get_context('spawn')) as pool:
            for result in pool.map(process_retry, tasks, chunksize=1):
                log.write(json.dumps(result) + '\n')
                report[result['status']] += 1
                write_json(state / 'status.json', report)
        report['sample_publications'] = sum(r['status'] == 'published' for r in samples)
        report['status'] = 'failed' if report['failed'] else 'passed'
        write_json(state / 'status.json', report)
        if report['failed']:
            raise RuntimeError('Subset failures; inspect results.jsonl')


if __name__ == '__main__':
    try:
        main()
    except BaseException as error:
        state = (Path(__file__).resolve().parents[1] / 'forcing/status/ucrb'
                 / ('backfill-' + os.environ['SLURM_JOB_ID']) / 'status.json')
        report = json.loads(state.read_text()) if state.exists() else {}
        write_json(state, report | {'status': 'failed', 'error': str(error)})
        raise
