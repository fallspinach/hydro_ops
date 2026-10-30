"""Run the USGS live-data gate, restartable shards, or complete-cohort diagnostics."""
import argparse
import csv
import json
import os
from pathlib import Path

from hydro_ops.download.cdec_fnf import atomic_bytes, write_json
from hydro_ops.download.usgs_discharge import accepted, safe_acquire
from hydro_ops.download.usgs_inventory import csv_bytes, sha256


def finalize(root, state, plan):
    results = []
    for shard in range(plan['shards']):
        report = json.loads((state / f'shard-{shard}.json').read_text())
        if report.get('status') != 'passed' or report.get('plan_sha256') != plan['plan_sha256']:
            raise ValueError(f'Shard {shard} did not pass; use --resume-plan to retry')
        results.extend(report['results'])
    ids = {s['monitoring_location_id'] for s in plan['stations']}
    if len(results) != len(ids) or {r['station'] for r in results} != ids:
        raise ValueError('Station result coverage mismatch')
    diagnostics, annual = [], []
    for spec in plan['stations']:
        target = root / 'observations/processed/usgs/discharge/daily' / spec['monitoring_location_id']
        record = accepted(root, target / 'acceptance.json', spec, plan)
        if record is None or not record.get('source_to_published_rows_verified'):
            raise ValueError('Missing station acceptance')
        diagnostics.append(json.loads((target / 'diagnostics.json').read_text()))
        with (target / 'annual_completeness.csv').open() as handle:
            annual.extend(csv.DictReader(handle))
    atomic_bytes(state / 'station_completeness.csv', csv_bytes(diagnostics))
    atomic_bytes(state / 'annual_completeness.csv', csv_bytes(annual))
    for domain in ('cnrfc', 'b120', 'ucrb', 'yampa'):
        selected = {s['monitoring_location_id'] for s in plan['stations'] if domain in s['reporting_domains']}
        atomic_bytes(state / 'domains' / domain / 'station_completeness.csv',
                     csv_bytes([r for r in diagnostics if r['monitoring_location_id'] in selected]))
    report = {'status': 'passed', 'station_count': len(ids), 'plan_sha256': plan['plan_sha256'],
              'rows': sum(r['rows'] for r in diagnostics),
              'stations_with_valid_primary_data': sum(r['first_valid_date'] is not None for r in diagnostics),
              'stations_with_no_valid_diagnostic_data': sum(r['first_valid_date'] is None for r in diagnostics),
              'diagnostics': str((state / 'station_completeness.csv').relative_to(root)),
              'annual_diagnostics': str((state / 'annual_completeness.csv').relative_to(root)),
              'daily_updates_enabled': False, 'files_sha256': {
                  str(p.relative_to(state)): sha256(p) for p in state.rglob('*.csv')}}
    write_json(state / 'status.json', report)
    write_json(state.parent / 'latest-backfill.json', report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--stage', choices=('smoke', 'backfill', 'finalize'), required=True)
    args = parser.parse_args()
    if not os.environ.get('SLURM_JOB_ID'):
        raise RuntimeError('Use slurm/download_usgs_discharge.sh')
    root = Path(__file__).resolve().parents[1]
    state = args.plan.resolve().parent
    plan = json.loads(args.plan.read_text())
    plan['plan_sha256'] = sha256(args.plan)
    if sha256(state / 'feature_gage_row.csv') != plan['mapping_sha256']:
        raise ValueError('Pinned reach mapping changed')
    if args.stage == 'finalize':
        try:
            finalize(root, state, plan)
        except BaseException as error:
            write_json(state / 'status.json', {'status': 'failed', 'error': str(error)})
            raise
        return
    shard = int(os.environ.get('SLURM_ARRAY_TASK_ID', '0'))
    if args.stage == 'smoke':
        specifications = [plan['stations'][i] for i in plan['smoke_indices']]
        output = state / 'smoke.json'
    else:
        gate = json.loads((state / 'smoke.json').read_text())
        if gate.get('status') != 'passed' or gate.get('plan_sha256') != plan['plan_sha256']:
            raise ValueError('Matching live-data smoke gate must pass')
        specifications = plan['stations'][shard::plan['shards']]
        output = state / f'shard-{shard}.json'
    report = {'status': 'running', 'stage': args.stage, 'job_id': os.environ['SLURM_JOB_ID'],
              'plan_sha256': plan['plan_sha256'], 'expected': len(specifications), 'results': []}
    write_json(output, report)
    for spec in specifications:
        result = safe_acquire(root, state, spec, plan)
        report['results'].append(result)
        print(json.dumps(result), flush=True)
        if len(report['results']) % 10 == 0:
            write_json(output, report)
    failed = sum(r['status'] != 'passed' for r in report['results'])
    report.update(status='failed' if failed else 'passed', failed=failed)
    if args.stage == 'smoke' and not any(r.get('rows', 0) > 100 for r in report['results']):
        report['status'] = 'failed'
    write_json(output, report)
    if report['status'] != 'passed':
        raise RuntimeError('USGS station failures; accepted records retained for retry')


if __name__ == '__main__':
    main()
