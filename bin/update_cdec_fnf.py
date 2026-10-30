"""Refresh all daily/monthly CDEC FNF histories with a single-writer lock."""
import argparse
import fcntl
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

import requests

from hydro_ops.download.cdec_fnf import PST, acquire, series_from_metadata, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--metadata', type=Path)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--plan-only', action='store_true')
    args = parser.parse_args()
    if args.workers not in (1, 2, 3, 4):
        parser.error('Use 1--4 workers')
    root = Path(__file__).resolve().parents[1]
    ids = json.loads((root / 'config/cdec_fnf_stations.json').read_text())['station_ids']
    if len(ids) != 24 or len(set(ids)) != 24:
        raise ValueError('Expected the configured 24 stations')
    if args.metadata:
        metadata = args.metadata
    else:
        candidates = sorted((root / 'observations/static/stations/cdec').glob('*/stations.json'))
        metadata = next((p for p in reversed(candidates) if json.loads(p.read_text()).get('status') == 'passed'), None)
    if metadata is None:
        raise ValueError('Collect station metadata before downloading FNF')
    series = series_from_metadata(json.loads(metadata.read_text()), ids)
    if args.plan_only:
        print(json.dumps({'series': series, 'metadata': str(metadata), 'workers': args.workers}, indent=2))
        return
    if not os.environ.get('SLURM_JOB_ID'):
        raise RuntimeError('Submit data processing through bin/submit_cdec_fnf.py')
    status = root / 'observations/status/cdec/fnf'
    status.mkdir(parents=True, exist_ok=True)
    with (status / 'update.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        started = time.monotonic()
        run = status / ('job-' + os.environ['SLURM_JOB_ID'])
        run.mkdir(parents=True, exist_ok=False)
        end = datetime.now(PST).date()
        report = {'status': 'running', 'job_id': os.environ['SLURM_JOB_ID'],
                  'started_utc': datetime.now(UTC).isoformat(), 'metadata': str(metadata.relative_to(root)),
                  'expected_series': len(series), 'completed': 0, 'failed': 0,
                  'policy': 'Refresh full advertised history daily to capture older revisions', 'series': []}
        write_json(run / 'plan.json', report | {'requests': series, 'end': end.isoformat()})

        def publish():
            report['elapsed_seconds'] = time.monotonic() - started
            write_json(run / 'status.json', report)
            write_json(status / 'latest.json', report)

        publish()
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(acquire, root, spec, end): spec for spec in series}
            for future in as_completed(futures):
                spec = futures[future]
                try:
                    result = future.result()
                    report['completed'] += 1
                except (requests.RequestException, OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
                    result = spec | {'status': 'failed', 'error': str(error)}
                    report['failed'] += 1
                report['series'].append(result)
                publish()
                print(json.dumps(result), flush=True)
        report['status'] = 'failed' if report['failed'] else 'passed'
        publish()
        if report['failed']:
            raise RuntimeError('Some FNF series failed; accepted other series remain available')


if __name__ == '__main__':
    main()
