"""Refresh accepted NRT subsets for expanded CNRFC, UCRB or nested Yampa."""
import argparse
import fcntl
import json
import multiprocessing
import os
import shutil
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from backfill_b120_forcing import checked_process
from backfill_cnrfc_forcing import digest, discover, identity, process_retry
from backfill_ucrb_forcing import hourly_gaps
from prepare_cnrfc_hu18_test import approved as approved_cnrfc
from run_b120_production import approved as approved_b120
from run_yampa_production import approved as approved_yampa

from hydro_ops.wrf_hydro.production import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--domain', choices=('cnrfc_hu18_v1', 'ucrb', 'yampa', 'b120'), required=True)
    parser.add_argument('--end-date', help='Last calendar-day collection; default discovers latest parent at execution')
    parser.add_argument('--workers', type=int, default=16)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    domain = root / 'nwm/static/domains' / args.domain
    stem = 'cnrfc_hu18' if args.domain == 'cnrfc_hu18_v1' else args.domain
    mask = domain / 'masks' / (stem + '_masks.nc')
    if args.domain == 'cnrfc_hu18_v1':
        approved_cnrfc(root)
    elif args.domain == 'yampa':
        approved_yampa(root)
    elif args.domain == 'b120':
        approved_b120(root)
    else:
        record = json.loads((domain / 'manifest.json').read_text())
        if not record.get('approved_for_production') or record['mask_sha256'] != digest(mask):
            raise ValueError('Unapproved/changed UCRB masks')
    parent_name = {'yampa': 'ucrb', 'b120': 'cnrfc'}.get(args.domain, 'conus')
    parent = root / 'forcing/outputs' / parent_name
    if args.end_date is None:
        latest = max((parent / 'nrt/hourly').glob('*/*/????????.LDASIN_DOMAIN1'))
        stamp = latest.name[:8]
        args.end_date = f'{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}'
    output = root / 'forcing/outputs' / args.domain
    job = os.environ['SLURM_JOB_ID']
    state = root / 'forcing/status' / args.domain / ('nrt-catchup-' + job)
    state.mkdir(parents=True, exist_ok=False)
    scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{job}/nrt-subset')
    scratch.mkdir(parents=True, exist_ok=False)
    if shutil.disk_usage(scratch).free < 20 * 1024**3:
        raise RuntimeError('Insufficient scratch')
    output.mkdir(parents=True, exist_ok=True)
    # Match the established writer's lock, not a new independent lock.
    lockname = '.subset-pilot.lock' if args.domain == 'cnrfc_hu18_v1' else '.subset.lock'
    with (output / lockname).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        paths = [p for p in discover(parent, ('nrt',))
                 if p.name.split('.')[0] <= args.end_date.replace('-', '')[:len(p.name.split('.')[0])]]
        if not paths or hourly_gaps(paths, parent, 'nrt'):
            raise ValueError('Empty/incomplete parent NRT archive')
        terminal = parent / 'nrt/hourly' / (args.end_date[:4] + '/' + args.end_date[5:7]) / (
            args.end_date.replace('-', '') + '.LDASIN_DOMAIN1')
        if terminal not in paths:
            raise ValueError('Required terminal forcing collection absent')
        mask_hash = digest(mask)
        tasks = [(p, output / p.relative_to(parent), mask, mask_hash, scratch, identity(p)) for p in paths]
        report = {'status': 'running', 'domain': args.domain, 'source_domain': parent_name,
                  'job_id': job, 'end_date': args.end_date, 'total': len(tasks),
                  'published': 0, 'skipped': 0, 'failed': 0, 'mask_sha256': mask_hash}
        started = time.monotonic()
        write_json(state / 'plan.json', report | {'sources': [str(p) for p in paths]})
        write_json(state / 'status.json', report)
        function = checked_process if parent_name != 'conus' else process_retry
        with (state / 'results.jsonl').open('a', buffering=1) as log, ProcessPoolExecutor(
                max_workers=args.workers, mp_context=multiprocessing.get_context('spawn')) as pool:
            for result in pool.map(function, tasks, chunksize=1):
                log.write(json.dumps(result) + '\n')
                report[result['status']] += 1
                report['elapsed_seconds'] = time.monotonic() - started
                write_json(state / 'status.json', report)
        report['status'] = 'failed' if report['failed'] else 'passed'
        write_json(state / 'status.json', report)
        if report['failed']:
            raise RuntimeError('NRT subset failures; model dependency remains blocked')


if __name__ == '__main__':
    main()
