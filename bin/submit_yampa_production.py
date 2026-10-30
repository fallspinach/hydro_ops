"""Submit Yampa parameters, warm-start gate and sequential annual retro runs."""
# Model scheduling APIs use naive UTC.
# ruff: noqa: DTZ001
import fcntl
import json
import subprocess
from datetime import datetime
from pathlib import Path

from run_b120_production import CAMPAIGN, END
from run_yampa_production import approved
from submit_ucrb_production import forcing_ready

from hydro_ops.wrf_hydro.production import write_json


def main():
    root = Path(__file__).resolve().parents[1]
    approved(root)
    parent_report = root / 'forcing/status/yampa/backfill-5042206/status.json'
    forcing = json.loads(parent_report.read_text())
    if (forcing.get('status') != 'passed' or forcing.get('domain') != 'yampa'
            or forcing.get('failed') != 0 or any(forcing.get('hourly_source_gaps', {}).values())
            or forcing.get('published', 0) + forcing.get('skipped', 0) != forcing.get('total')):
        raise ValueError('Yampa forcing publication must pass')
    samples = json.loads(parent_report.with_name('sample_acceptance.json').read_text())
    if samples.get('status') != 'passed' or len(samples.get('samples', [])) != 6:
        raise ValueError('Direct CONUS/Yampa sample gate must pass')
    if not forcing_ready(root / 'forcing/outputs/yampa/retro/hourly', datetime(1979, 1, 2), END):
        raise ValueError('Missing interior/boundary hourly forcing or receipt')
    state = root / 'nwm/status/yampa' / CAMPAIGN
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'submission.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = state / 'submission.json'
        if manifest.exists():
            raise FileExistsError('Existing Yampa submission journal; inspect before retrying')
        report = {'status': 'submitting', 'domain': 'yampa', 'mpi_ranks': 32,
                  'start': '1979-01-02T00:00:00', 'end_exclusive': END.isoformat(),
                  'forcing_acceptance': str(parent_report.relative_to(root)),
                  'operational_cutover': False, 'jobs': []}
        write_json(manifest, report)

        def submit(stage, dependencies=(), gate=None):
            command = ['sbatch', '--parsable', f'--chdir={root}',
                       f'--job-name=wrfh-yampa-retro-{stage}-32mpi-monthly-checkpoints',
                       '--time=04:00:00' if stage == 'parameters' else
                       '--time=02:00:00' if stage == 'gate' else '--time=24:00:00']
            if stage == 'parameters':
                command += ['--ntasks=1', '--cpus-per-task=32']
            if dependencies:
                command += ['--dependency=afterok:' + ':'.join(dependencies)]
            command += ['slurm/run_yampa_production.sh', stage]
            if gate:
                command += ['--gate', str(gate)]
            entry = {'stage': stage, 'dependencies': list(dependencies),
                     'command': command, 'status': 'submission_uncertain'}
            report['jobs'].append(entry)
            write_json(manifest, report)
            job = subprocess.check_output(command, cwd=root, text=True).strip().split(';')[0]
            if not job.isdecimal():
                raise ValueError(f'Unexpected sbatch response {job}')
            entry.update(job_id=job, status='submitted')
            write_json(manifest, report)
            print(json.dumps(entry), flush=True)
            return job

        parameters = submit('parameters')
        previous = submit('gate', [parameters])
        gate = state / ('job-' + previous) / 'acceptance.json'
        for year in range(1979, 2027):
            previous = submit(str(year), [previous], gate)
        report['status'] = 'submitted'
        write_json(manifest, report)


if __name__ == '__main__':
    main()
