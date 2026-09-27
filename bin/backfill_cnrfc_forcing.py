"""Restartable, audited CNRFC subsets of existing CONUS hourly and summary files."""
import argparse
import fcntl
import hashlib
import json
import multiprocessing
import os
import shutil
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from subset_nwm_forcing import subset


def identity(path):
    stat = path.stat()
    return [stat.st_ino, stat.st_size, stat.st_mtime_ns]


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


_mask_cache = {}


def mask_matches(path, expected):
    before = identity(path)
    key = (str(path), tuple(before))
    if key not in _mask_cache:
        value = digest(path)
        if identity(path) != before:
            return False
        _mask_cache.clear()
        _mask_cache[key] = value
    return _mask_cache[key] == expected


def atomic_json(path, record):
    partial = path.with_name(path.name + '.part')
    partial.write_text(json.dumps(record, indent=2) + '\n')
    partial.replace(path)


def discover(root, streams=('retro', 'nrt'), frequencies=('hourly', 'daily', 'monthly')):
    paths = []
    for stream in streams:
        for frequency, pattern in (
            ('hourly', '*/*/????????.LDASIN_DOMAIN1'),
            ('daily', '*/*/????????.LDASIN_DOMAIN1.daily'),
            ('monthly', '*/??????.LDASIN_DOMAIN1.monthly'),
        ):
            if frequency not in frequencies:
                continue
            paths.extend(sorted((root / stream / frequency).glob(pattern)))
    return paths


def process(task):
    source, target, mask, mask_hash, scratch, expected = task
    started = time.monotonic()
    result = {'source': str(source), 'output': str(target)}
    try:
        if identity(source) != expected:
            raise RuntimeError('Source changed after campaign discovery; retry next campaign')
        if not mask_matches(mask, mask_hash):
            raise RuntimeError('Domain masks changed during campaign')
        receipt = target.with_name(target.name + '.subset.json')
        old = json.loads(receipt.read_text()) if receipt.exists() else {}
        target_before = identity(target) if target.exists() else None
        if target_before is not None:
            if old.get('status') != 'passed' or old.get('source') != str(source.resolve()):
                raise RuntimeError('Existing output lacks a recognized audit; refusing replacement')
            # Pilot receipts have no mask hash: rebuild once to establish full provenance.
            if (old.get('source_identity') == expected and old.get('mask_sha256') == mask_hash
                    and (old.get('output_identity') == target_before
                         or digest(target) == old.get('sha256'))):
                if identity(source) != expected:
                    raise RuntimeError('Source changed while checking existing subset')
                if identity(target) != target_before:
                    raise RuntimeError('Destination changed while checking existing subset')
                if old.get('output_identity') != target_before:
                    old['output_identity'] = target_before
                    atomic_json(receipt, old)
                return result | {'status': 'skipped', 'seconds': time.monotonic() - started}
        with tempfile.TemporaryDirectory(prefix='cnrfc-subset-', dir=scratch) as temporary:
            candidate = Path(temporary) / target.name
            report = subset(source, candidate, mask)
            checksum = digest(candidate)
            if identity(source) != expected:
                raise RuntimeError('Source changed during subsetting')
            target.parent.mkdir(parents=True, exist_ok=True)
            # Unique staging file; a terminated run cannot block a retry.
            with tempfile.NamedTemporaryFile(prefix=target.name + '.', suffix='.part',
                                             dir=target.parent, delete=False) as handle:
                staging = Path(handle.name)
            try:
                shutil.copy2(candidate, staging)
                if digest(staging) != checksum:
                    raise RuntimeError('Transfer checksum mismatch')
                if identity(source) != expected:
                    raise RuntimeError('Source changed before publication')
                if not mask_matches(mask, mask_hash):
                    raise RuntimeError('Masks changed before publication')
                if (identity(target) if target.exists() else None) != target_before:
                    raise RuntimeError('Destination changed during subsetting')
                staging.replace(target)
                report.update(output=str(target.resolve()), source_identity=expected,
                              mask_sha256=mask_hash, sha256=checksum,
                              output_bytes=target.stat().st_size,
                              output_identity=identity(target),
                              total_seconds=time.monotonic() - started)
                atomic_json(receipt, report)
            finally:
                staging.unlink(missing_ok=True)
        return result | {'status': 'published', 'seconds': time.monotonic() - started}
    except Exception as error:
        return result | {'status': 'failed', 'error': str(error),
                         'seconds': time.monotonic() - started}


def process_retry(task):
    """Retry only atomic CONUS replacement races, not scientific/audit failures."""
    for attempt in range(3):
        try:
            current = (*task[:-1], identity(task[0]))
        except OSError as error:
            return {'source': str(task[0]), 'output': str(task[1]), 'status': 'failed', 'error': str(error)}
        result = process(current)
        result.update(attempts=attempt + 1, source_identity=current[-1])
        if result['status'] != 'failed' or not result.get('error', '').startswith('Source changed'):
            return result
        time.sleep(1)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=16)
    parser.add_argument('--scratch', type=Path, required=True)
    parser.add_argument('--plan-only', action='store_true')
    parser.add_argument('--stream', choices=('all', 'retro', 'nrt'), default='all')
    parser.add_argument('--frequency', choices=('all', 'hourly', 'daily', 'monthly'), default='all')
    parser.add_argument('--incremental', action='store_true', help='Operational synchronization report and bounded source-race retries')
    args = parser.parse_args()
    if args.workers < 1:
        parser.error('--workers must be positive')
    root = Path(__file__).resolve().parents[1]
    parent = root / 'forcing/outputs/conus'
    output = root / 'forcing/outputs/cnrfc'
    mask = root / 'nwm/static/domains/cnrfc/masks/cnrfc_masks.nc'
    paths = discover(parent, ('retro', 'nrt') if args.stream == 'all' else (args.stream,),
                     ('hourly', 'daily', 'monthly') if args.frequency == 'all' else (args.frequency,))
    counts = {}
    for path in paths:
        key = '/'.join(path.relative_to(parent).parts[:2])
        counts[key] = counts.get(key, 0) + 1
    print(json.dumps({'files': len(paths), 'counts': counts, 'workers': args.workers}), flush=True)
    if args.plan_only:
        return 0
    args.scratch.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(args.scratch).free < 20 * 1024**3:
        raise RuntimeError('Less than 20 GiB free scratch')
    output.mkdir(parents=True, exist_ok=True)
    with (output / '.subset-pilot.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        campaign = root / 'forcing/status/cnrfc' / (('sync-' if args.incremental else 'backfill-') + os.environ['SLURM_JOB_ID'])
        campaign.mkdir(parents=True, exist_ok=False)
        mask_hash = digest(mask)
        tasks = [(path, output / path.relative_to(parent), mask, mask_hash,
                  args.scratch, identity(path)) for path in paths]
        atomic_json(campaign / 'plan.json', {'counts': counts, 'mask_sha256': mask_hash,
                    'sources': [{'path': str(task[0]), 'identity': task[-1]} for task in tasks]})
        started = time.monotonic()
        report = {'status': 'running', 'total': len(tasks), 'completed': 0,
                  'published': 0, 'skipped': 0, 'failed': 0, 'workers': args.workers,
                  'counts': counts, 'job_id': os.environ['SLURM_JOB_ID']}
        atomic_json(campaign / 'status.json', report)
        with (campaign / 'results.jsonl').open('a', buffering=1) as log:
            with ProcessPoolExecutor(max_workers=args.workers,
                                     mp_context=multiprocessing.get_context('spawn')) as pool:
                for result in pool.map(process_retry if args.incremental else process, tasks, chunksize=1):
                    log.write(json.dumps(result) + '\n')
                    report[result['status']] += 1
                    report['completed'] += 1
                    report['elapsed_seconds'] = time.monotonic() - started
                    atomic_json(campaign / 'status.json', report)
                    if result['status'] == 'failed' or report['completed'] % 100 == 0:
                        print(json.dumps(result if result['status'] == 'failed' else report), flush=True)
        report['status'] = 'failed' if report['failed'] else 'passed'
        atomic_json(campaign / 'status.json', report)
        if args.incremental:
            atomic_json(campaign.parent / f'latest-sync-{args.stream}-{args.frequency}.json', report)
        print(json.dumps(report), flush=True)
        return int(bool(report['failed']))


if __name__ == '__main__':
    raise SystemExit(main())
