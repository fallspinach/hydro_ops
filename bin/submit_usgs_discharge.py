"""Pin the approved NWM-mapped cohort and submit gated USGS daily-flow backfill."""
import argparse
import csv
import fcntl
import io
import json
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hydro_ops.download.cdec_fnf import atomic_bytes, write_json
from hydro_ops.download.usgs_inventory import DOMAINS, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection', type=Path, required=False)
    parser.add_argument('--mapping', type=Path)
    parser.add_argument('--resume-plan', type=Path, help='Retry failed/unstarted shards using accepted caches')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    status = root / 'observations/status/usgs/discharge'
    status.mkdir(parents=True, exist_ok=True)
    with (status / 'submission.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        active = subprocess.check_output(['squeue', '--me', '--noheader', '--format=%j'], text=True)
        if any(name.startswith('obs-usgs-discharge-') for name in active.splitlines()):
            raise RuntimeError('An existing USGS discharge campaign is active; inspect before retrying')
        if args.resume_plan:
            plan_file = args.resume_plan.resolve()
            plan = json.loads(plan_file.read_text())
            state = plan_file.parent
        else:
            if not args.selection or not args.mapping:
                parser.error('A new campaign requires --selection and --mapping')
            selection = args.selection.resolve()
            acceptance = json.loads((selection / 'acceptance.json').read_text())
            if acceptance.get('status') != 'passed' or acceptance.get('unknown_end_policy') != 'separate':
                raise ValueError('Use the passed, confirmed-overlap model-period selection')
            for path, expected in acceptance['files_sha256'].items():
                if sha256(selection / path) != expected:
                    raise ValueError('Selection export checksum mismatch')
            parent = Path(acceptance['source_snapshot'])
            full = json.loads((parent / 'acceptance.json').read_text())
            if (sha256(parent / 'acceptance.json') != acceptance['source_acceptance_sha256']
                    or sha256(parent / 'time_series.json') != full['files_sha256']['time_series.json']):
                raise ValueError('Parent time-series inventory changed')
            mapping_payload = args.mapping.read_bytes()
            mappings = {}
            for row in csv.DictReader(io.StringIO(mapping_payload.decode())):
                gage = row['gage_id'].strip()
                # The crosswalk also contains non-USGS alphanumeric network IDs.
                if not re.fullmatch(r'\d{8,15}', gage):
                    continue
                key = 'USGS-' + gage
                if key in mappings:
                    raise ValueError('Duplicate station mapping needs explicit reconciliation')
                mappings[key] = {'nwm_feature_id': row['feature_id'].strip(),
                                 'mapping_row': row['row'].strip()}
            stations = json.loads((selection / 'stations.json').read_text())['stations']
            specs = {s['monitoring_location_id']: s | mappings[s['monitoring_location_id']]
                     | {'series_metadata': {}, 'reporting_domains': []}
                     for s in stations if s['monitoring_location_id'] in mappings}
            if len(specs) != 7495:
                raise ValueError('The approved matched cohort is 7,495 stations; review changed inputs')
            for row in json.loads((parent / 'time_series.json').read_text())['time_series']:
                if row['monitoring_location_id'] in specs:
                    specs[row['monitoring_location_id']]['series_metadata'][row['id']] = row['provider_time_series']
            for domain in DOMAINS:
                ids = set((selection / 'domains' / domain / 'station_ids.txt').read_text().splitlines())
                for key in ids & specs.keys():
                    specs[key]['reporting_domains'].append(domain)
            if any(not row['series_metadata'] for row in specs.values()):
                raise ValueError('Missing station-series metadata')
            specifications = sorted(specs.values(), key=lambda r: (
                not bool(r['reporting_domains']), r['monitoring_location_id']))
            smoke = set()
            for domain in DOMAINS:
                smoke.add(next(i for i, s in enumerate(specifications) if domain in s['reporting_domains']))
            # Exercise long history, discontinued records, recent start and multiple series.
            for predicate in (
                lambda s: any((v.get('end') or '')[:4] < '2000' and v.get('end') for v in s['series_metadata'].values()),
                lambda s: all((v.get('begin') or '')[:4] >= '2010' for v in s['series_metadata'].values()),
                lambda s: len(s['series_metadata']) > 1,
            ):
                index = next((i for i, s in enumerate(specifications) if predicate(s)), None)
                if index is not None:
                    smoke.add(index)
            state = status / ('backfill-' + datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ'))
            state.mkdir(exist_ok=False)
            atomic_bytes(state / 'feature_gage_row.csv', mapping_payload)
            plan = {'mode': 'backfill', 'start': '1979-01-01',
                    'end': (datetime.now(UTC).date() - timedelta(days=1)).isoformat(),
                    'selection': str(selection.relative_to(root)),
                    'selection_acceptance_sha256': sha256(selection / 'acceptance.json'),
                    'mapping_sha256': sha256(state / 'feature_gage_row.csv'),
                    'mapping_original': str(args.mapping), 'shards': 8, 'concurrency': 2,
                    'request_interval_seconds': 5, 'station_count': len(specifications),
                    'smoke_indices': sorted(smoke), 'stations': specifications}
            plan_file = state / 'plan.json'
            write_json(plan_file, plan)
        journal = state / ('submission-' + datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ') + '.json')
        record = {'status': 'submitting', 'plan': str(plan_file.relative_to(root)),
                  'plan_sha256': sha256(plan_file), 'jobs': []}
        write_json(state / 'status.json', {'status': 'submitting',
            'station_count': plan['station_count'], 'start': plan['start'], 'end': plan['end'],
            'plan_sha256': record['plan_sha256'], 'daily_updates_enabled': False})

        def submit(stage, dependency=None):
            command = ['sbatch', '--parsable', f'--chdir={root}',
                       '--job-name=obs-usgs-discharge-' + stage]
            if stage == 'backfill':
                command += ['--array=0-7%2', '--time=08:00:00']
            if dependency:
                command += ['--dependency=' + dependency]
            command += ['slurm/download_usgs_discharge.sh', '--plan', str(plan_file), '--stage', stage]
            entry = {'stage': stage, 'command': command, 'status': 'submission_uncertain'}
            record['jobs'].append(entry)
            write_json(journal, record)
            job = subprocess.check_output(command, text=True, cwd=root).strip().split(';')[0]
            if not job.isdecimal():
                raise ValueError('Unexpected sbatch response')
            entry.update(status='submitted', job_id=job)
            write_json(journal, record)
            return job

        gate = submit('smoke')
        backfill = submit('backfill', 'afterok:' + gate)
        submit('finalize', 'afterany:' + backfill)
        record['status'] = 'submitted'
        write_json(journal, record)
        write_json(state / 'status.json', {'status': 'submitted',
            'station_count': plan['station_count'], 'start': plan['start'], 'end': plan['end'],
            'plan_sha256': record['plan_sha256'], 'daily_updates_enabled': False,
            'jobs': {entry['stage']: entry['job_id'] for entry in record['jobs']}})
        print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
