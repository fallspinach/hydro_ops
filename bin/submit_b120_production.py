"""Submit the B120 gate and sequential 1979--2026 retro chain once."""
# ruff: noqa: DTZ001
import fcntl
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from run_b120_production import CAMPAIGN, END, approved

from hydro_ops.wrf_hydro.production import write_json


def main():
    root = Path(__file__).resolve().parents[1]
    approved(root)
    day = datetime(1979, 1, 2)
    while day <= END:
        source = root / 'forcing/outputs/b120/retro/hourly' / day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
        if not source.is_file() or not source.with_name(source.name + '.subset.json').is_file():
            raise ValueError(f'Missing forcing or receipt: {source}')
        day += timedelta(days=1)
    state = root / 'nwm/status/b120' / CAMPAIGN
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'submission.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = state / 'submission.json'
        if manifest.exists():
            raise FileExistsError('Existing submission journal; inspect before retrying')
        report = {'status': 'submitting', 'domain': 'b120', 'mpi_ranks': 32,
                  'start': '1979-01-02T00:00:00', 'end_exclusive': END.isoformat(),
                  'operational_cutover': False, 'jobs': []}
        write_json(manifest, report)

        def submit(stage, previous=None, gate=None):
            command = ['sbatch', '--parsable', f'--chdir={root}',
                       f'--job-name=wrfh-b120-retro-{stage}-32mpi-monthly-checkpoints',
                       '--time=02:00:00' if stage == 'gate' else '--time=24:00:00']
            if previous:
                command.append('--dependency=afterok:' + previous)
            command += ['slurm/run_b120_production.sh', stage]
            if gate:
                command += ['--gate', str(gate)]
            entry = {'stage': stage, 'command': command, 'afterok': previous,
                     'status': 'submission_uncertain'}
            report['jobs'].append(entry)
            write_json(manifest, report)
            job = subprocess.check_output(command, cwd=root, text=True).strip().split(';')[0]
            if not job.isdecimal():
                raise ValueError(f'Unexpected sbatch response: {job}')
            entry.update(job_id=job, status='submitted')
            write_json(manifest, report)
            print(json.dumps(entry), flush=True)
            return job

        previous = submit('gate')
        gate = state / ('job-' + previous) / 'acceptance.json'
        for year in range(1979, 2027):
            previous = submit(str(year), previous, gate)
        report['status'] = 'submitted'
        write_json(manifest, report)


if __name__ == '__main__':
    main()
