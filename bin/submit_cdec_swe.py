"""Plan and submit CDEC SWE smoke tests, historical backfills or daily updates."""
import argparse
import fcntl
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from hydro_ops.download.cdec_fnf import PST, write_json
from hydro_ops.download.cdec_swe import digest, series, tasks_for


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('smoke', 'backfill', 'update'), default='update')
    parser.add_argument('--refresh', action='store_true', help='Re-fetch accepted historical chunks as well')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    status = root / 'observations/status/cdec/swe'
    status.mkdir(parents=True, exist_ok=True)
    with (status / 'submission.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        active = subprocess.check_output(['squeue', '--me', '--noheader',
            '--name=obs-cdec-swe-smoke,obs-cdec-swe-backfill,obs-cdec-swe-update,obs-cdec-swe-finalize',
            '--format=%i'], text=True).strip()
        if active:
            print(json.dumps({'status': 'skipped', 'active_jobs': active.split()})); return
        candidates = sorted((root / 'observations/static/stations/cdec/swe').glob('*/stations.json'))
        metadata = next((p for p in reversed(candidates) if json.loads(p.read_text()).get('status') == 'passed'), None)
        if metadata is None:
            raise ValueError('Collect SWE station metadata first')
        payload = metadata.read_bytes()
        metadata_sha = digest(payload)
        specifications = series(json.loads(payload))
        if args.mode != 'smoke':
            gate = json.loads((status / 'latest-smoke.json').read_text())
            if gate.get('status') != 'passed' or gate['metadata_sha256'] != metadata_sha:
                raise ValueError('Matching SWE smoke gate must pass first')
        if args.mode == 'update':
            gate = json.loads((status / 'latest-backfill.json').read_text())
            if gate.get('status') != 'passed' or gate['metadata_sha256'] != metadata_sha:
                raise ValueError('Matching historical SWE backfill must pass before routine updates')
        tasks = tasks_for(root, specifications, datetime.now(PST).date(), args.mode)
        if args.dry_run:
            print(json.dumps({'mode': args.mode, 'series': len(specifications), 'tasks': len(tasks),
                              'metadata': str(metadata), 'shards': 8 if args.mode == 'backfill' else 1})); return
        journal = status / 'submission.json'
        if journal.exists() and json.loads(journal.read_text()).get('status') == 'submission_uncertain':
            raise RuntimeError('Review uncertain previous submission before retry')
        state = status / (args.mode + '-' + datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ'))
        state.mkdir(exist_ok=False)
        plan = {'mode': args.mode, 'metadata': str(metadata.relative_to(root)), 'metadata_sha256': metadata_sha,
                'series_count': len(specifications), 'tasks': tasks,
                'shards': 8 if args.mode == 'backfill' else 1, 'refresh': args.refresh or args.mode != 'backfill'}
        write_json(state / 'plan.json', plan)
        write_json(state / 'status.json', {'status': 'submitted', 'mode': args.mode, 'tasks': len(tasks)})
        command = ['sbatch', '--parsable', f'--chdir={root}', f'--job-name=obs-cdec-swe-{args.mode}']
        if args.mode == 'backfill':
            command += ['--array=0-7%2', '--time=24:00:00']
        elif args.mode == 'smoke':
            command += ['--time=01:00:00']
        command += ['slurm/update_cdec_swe.sh', '--plan', str(state / 'plan.json')]
        record = {'status': 'submission_uncertain', 'command': command, 'plan': str(state / 'plan.json')}
        write_json(journal, record)
        job = subprocess.check_output(command, cwd=root, text=True).strip().split(';')[0]
        if not job.isdecimal():
            raise ValueError(f'Unexpected sbatch response {job}')
        record.update(status='submitted', job_id=job)
        write_json(journal, record); write_json(state / 'submission.json', record)
        if args.mode == 'backfill':
            command = ['sbatch', '--parsable', f'--chdir={root}', '--job-name=obs-cdec-swe-finalize',
                       '--time=02:00:00', '--dependency=afterany:' + job,
                       'slurm/update_cdec_swe.sh', '--plan', str(state / 'plan.json'), '--finalize']
            record.update(status='submission_uncertain', finalizer_command=command)
            write_json(journal, record); write_json(state / 'submission.json', record)
            finalizer = subprocess.check_output(command, cwd=root, text=True).strip().split(';')[0]
            if not finalizer.isdecimal():
                raise ValueError(f'Unexpected finalizer response {finalizer}')
            record.update(status='submitted', finalizer_job_id=finalizer)
            write_json(journal, record); write_json(state / 'submission.json', record)
        print(json.dumps(record))


if __name__ == '__main__':
    main()
