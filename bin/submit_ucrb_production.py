"""Submit UCRB extraction, model gate and dependency-safe retro annual chain."""
# ruff: noqa: DTZ001
import argparse
import fcntl
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from backfill_ucrb_forcing import approved
from run_b120_production import CAMPAIGN, END

from hydro_ops.wrf_hydro.production import write_json


def forcing_ready(root, first, end):
    day = first
    while day <= end:
        path = root / day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
        if not path.is_file() or not path.with_name(path.name + '.subset.json').is_file():
            return False
        day += timedelta(days=1)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--forcing-job', required=True)
    args = parser.parse_args()
    if not args.forcing_job.isdecimal():
        parser.error('Expected numeric forcing job ID')
    root = Path(__file__).resolve().parents[1]
    approved(root / 'nwm/static/domains/ucrb')
    parent_state = root / 'forcing/status/ucrb' / ('backfill-' + args.forcing_job)
    report = json.loads((parent_state / 'status.json').read_text())
    if report.get('domain') != 'ucrb' or report.get('status') not in ('running', 'passed') or report.get('failed'):
        raise ValueError('Forcing campaign is not healthy')
    forcing = root / 'forcing/outputs/ucrb/retro/hourly'
    if not forcing_ready(forcing, datetime(1979, 1, 2), datetime(1979, 1, 5)):
        raise ValueError('Warm-gate forcing is not available')
    state = root / 'nwm/status/ucrb' / CAMPAIGN
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'submission.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = state / 'submission.json'
        if manifest.exists():
            raise FileExistsError('Existing submission journal; inspect before retry')
        record = {'status': 'submitting', 'domain': 'ucrb', 'mpi_ranks': 64,
                  'start': '1979-01-02T00:00:00', 'end_exclusive': END.isoformat(),
                  'forcing_job': args.forcing_job, 'operational_cutover': False, 'jobs': []}
        write_json(manifest, record)

        def submit(stage, dependencies=(), gate=None):
            command = ['sbatch', '--parsable', f'--chdir={root}',
                       f'--job-name=wrfh-ucrb-retro-{stage}-64mpi-monthly-checkpoints',
                       '--time=04:00:00' if stage == 'parameters' else
                       '--time=02:00:00' if stage == 'gate' else '--time=24:00:00']
            if stage == 'parameters':
                command += ['--ntasks=1', '--cpus-per-task=64']
            if dependencies:
                command += ['--dependency=afterok:' + ':'.join(dependencies)]
            command += ['slurm/run_ucrb_production.sh', stage]
            if gate:
                command += ['--gate', str(gate)]
            entry = {'stage': stage, 'dependencies': list(dependencies),
                     'command': command, 'status': 'submission_uncertain'}
            record['jobs'].append(entry); write_json(manifest, record)
            job = subprocess.check_output(command, cwd=root, text=True).strip().split(';')[0]
            if not job.isdecimal():
                raise ValueError(f'Unexpected sbatch response: {job}')
            entry.update(job_id=job, status='submitted'); write_json(manifest, record)
            print(json.dumps(entry), flush=True)
            return job

        prep = submit('parameters')
        previous = submit('gate', [prep])
        gate = state / ('job-' + previous) / 'acceptance.json'
        waiting_for_parent = False
        for year in range(1979, 2027):
            first = datetime(year, 1, 2 if year == 1979 else 1)
            stop = min(datetime(year + 1, 1, 1), END)
            deps = [previous]
            if not waiting_for_parent and not forcing_ready(forcing, first, stop):
                deps.append(args.forcing_job)
                waiting_for_parent = True
            previous = submit(str(year), deps, gate)
        record['status'] = 'submitted'; write_json(manifest, record)


if __name__ == '__main__':
    main()
