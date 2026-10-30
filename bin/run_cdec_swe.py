"""Execute or finalize a pinned, restartable CDEC SWE acquisition plan."""
import argparse
import json
import multiprocessing
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from hydro_ops.download.cdec_fnf import write_json
from hydro_ops.download.cdec_swe import digest, identity, safe_acquire


def finalize(root, plan, state):
    results = []
    for index in range(plan['shards']):
        path = state / f'shard-{index}.json'
        if not path.exists():
            raise ValueError(f'Missing shard report {index}')
        shard = json.loads(path.read_text())
        if shard.get('status') != 'passed':
            raise ValueError(f'Incomplete/failed shard {index}; inspect {path}')
        results.extend(shard['results'])
    expected = {t['request_id'] for t in plan['tasks']}
    if len(results) != len(expected) or {r['request_id'] for r in results} != expected:
        raise ValueError('Task/result coverage mismatch')
    rows = numeric = empty = 0
    for result in results:
        receipt = json.loads((root / result['receipt']).read_text())
        if receipt['status'] != 'passed' or not receipt.get('source_to_published_rows_verified'):
            raise ValueError('Unaccepted chunk receipt')
        if not (root / receipt['raw_file']).is_file():
            raise ValueError('Retained source response missing')
        for path, ident in receipt['output_identities'].items():
            if identity(root / path) != ident:
                raise ValueError('Processed file changed before campaign audit')
        rows += receipt['rows']; numeric += receipt['numeric_rows']; empty += receipt['rows'] == 0
    report = {'status': 'passed', 'mode': plan['mode'], 'tasks': len(expected),
              'rows_in_requested_windows': rows, 'numeric_rows_in_requested_windows': numeric,
              'empty_intervals': empty, 'series_count': plan['series_count'],
              'metadata_sha256': plan['metadata_sha256'], 'plan': str(state / 'plan.json')}
    write_json(state / 'status.json', report)
    write_json(root / 'observations/status/cdec/swe' / ('latest-' + plan['mode'] + '.json'), report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--finalize', action='store_true')
    args = parser.parse_args()
    if not os.environ.get('SLURM_JOB_ID'):
        raise RuntimeError('Run SWE processing on a compute node')
    root = Path(__file__).resolve().parents[1]
    plan = json.loads(args.plan.read_text())
    if digest((root / plan['metadata']).read_bytes()) != plan['metadata_sha256']:
        raise ValueError('Pinned metadata changed')
    state = args.plan.resolve().parent
    if args.finalize:
        try:
            finalize(root, plan, state)
        except (ValueError, OSError, KeyError) as error:
            write_json(state / 'status.json', {'status': 'failed', 'mode': plan['mode'], 'error': str(error)})
            raise
        return
    index = int(os.environ.get('SLURM_ARRAY_TASK_ID', '0'))
    tasks = plan['tasks'][index::plan['shards']]
    report = {'status': 'running', 'job_id': os.environ['SLURM_JOB_ID'],
              'shard': index, 'expected': len(tasks), 'results': []}
    start = time.monotonic()
    with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context('spawn')) as pool:
        for result in pool.map(safe_acquire, [(root, t, plan['refresh']) for t in tasks], chunksize=1):
            report['results'].append(result)
            report['elapsed_seconds'] = time.monotonic() - start
            write_json(state / f'shard-{index}.json', report)
            print(json.dumps(result), flush=True)
    failed = sum(r['status'] == 'failed' for r in report['results'])
    report.update(status='failed' if failed else 'passed', failed=failed)
    write_json(state / f'shard-{index}.json', report)
    if failed:
        if plan['shards'] == 1:
            write_json(state / 'status.json', {'status': 'failed', 'mode': plan['mode'], 'failed': failed})
        raise RuntimeError('SWE chunk failures; accepted chunks are retained for retry')
    if plan['shards'] == 1:
        finalize(root, plan, state)


if __name__ == '__main__':
    main()
