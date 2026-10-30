"""Submit isolated yearly forcing and sequential model continuation chains."""
import fcntl
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from prepare_cnrfc_hu18_test import approved, digest

from hydro_ops.wrf_hydro.production import restart_names, write_json


def schedule(start, end):
    return [(year, min(datetime(year + 1, 1, 1), end))  # noqa: DTZ001
            for year in range(start.year, end.year + 1)
            if datetime(year, 1, 1) < end]  # noqa: DTZ001


def main():
    root = Path(__file__).resolve().parents[1]
    plan_path = root / 'config/cnrfc_hu18_retro_continuation.json'
    plan = json.loads(plan_path.read_text())
    approved(root)
    if plan.get('authorized') is not True or plan['domain'] != 'cnrfc_hu18_v1':
        raise ValueError('Missing authorization')
    if not json.loads((root / plan['predecessor_acceptance']).read_text()).get('passed'):
        raise ValueError('1980 acceptance missing')
    gate = json.loads((root / plan['gate']).read_text())
    if gate.get('status') != 'passed' or not gate.get('spinup_warm_start_verified'):
        raise ValueError('Warm-start gate failed')
    start, end = [datetime.fromisoformat(plan[k]) for k in ('start', 'end_exclusive')]
    restart_root = root / 'nwm/restarts/cnrfc_hu18_v1/retro/hu18_pilot_v1' / start.strftime('%Y/%m')
    if not all((restart_root / name).is_file() for name in restart_names(start)):
        raise ValueError('Accepted continuation restart pair is missing')
    parent = root / 'forcing/outputs/conus/retro/hourly'
    day = start
    missing = []
    while day <= end:
        if not (parent / day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')).is_file():
            missing.append(str(day))
        day += timedelta(days=1)
    if missing:
        raise ValueError(f'Missing boundary/interior forcing dates: {missing}')
    state = root / 'nwm/status/cnrfc_hu18_v1/retro-continuation-1981-2026'
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'submission.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = state / 'submission.json'
        if manifest.exists():
            raise FileExistsError('Existing submission journal; inspect before retrying')
        report = {'status': 'submitting', 'plan': plan, 'plan_sha256': digest(plan_path), 'jobs': []}
        write_json(manifest, report)
        previous_forcing = previous_model = None

        def submit(stage, year, dependencies, script, arguments):
            cmd = ['sbatch', '--parsable', f'--chdir={root}',
                   f'--job-name={stage}-cnrfc-hu18-retro-{year}-continuation-64cpu']
            if dependencies:
                cmd.append('--dependency=afterok:' + ':'.join(dependencies))
            cmd += [script, *arguments]
            entry = {'stage': stage, 'year': year, 'dependencies': dependencies,
                     'command': cmd, 'status': 'submission_uncertain'}
            report['jobs'].append(entry)
            write_json(manifest, report)
            job = subprocess.check_output(cmd, cwd=root, text=True).strip().split(';')[0]
            if not job.isdecimal():
                raise ValueError(f'Unexpected sbatch response: {job}')
            entry.update(job_id=job, status='submitted')
            write_json(manifest, report)
            print(json.dumps(entry), flush=True)
            return job

        for year, stop in schedule(start, end):
            previous_forcing = submit('forcing', year,
                [previous_forcing] if previous_forcing else [], 'slurm/backfill_cnrfc_hu18_pilot.sh',
                ['--year', str(year), '--continuation-plan', str(plan_path)])
            previous_model = submit('wrfh', year,
                [previous_forcing] + ([previous_model] if previous_model else []),
                'slurm/run_cnrfc_hu18_pilot.sh',
                [str(year), '--continuation-plan', str(plan_path), '--gate', str(root / plan['gate'])])
        report['status'] = 'submitted'
        write_json(manifest, report)


if __name__ == '__main__':
    main()
