"""Preflight and submit the initial CNRFC NRT catch-up after warm-start acceptance."""
import argparse
import fcntl
import json
import subprocess
from pathlib import Path

from run_cnrfc_production import forcing_identity, simulation_window

from hydro_ops.wrf_hydro.production import (
    check_forcing,
    dates,
    restart_check,
    restart_names,
    write_json,
)


def command_for(root, end_date):
    gate = root / ('nwm/outputs/cnrfc/retro/tests/'
                   'archive_recovery_warmstart_19790201_48h/job_4642075/acceptance.json')
    return [
        'sbatch', '--parsable', '--time=06:00:00',
        f'--job-name=wrfh-cnrfc-nrt-20260302-{end_date.replace("-", "")}-64mpi-monthly-rst',
        'slurm/run_cnrfc_production.sh', '--year', '2026', '--stream', 'nrt',
        '--campaign', 'nrt_continuation_v1', '--start-date', '2026-03-02',
        '--end-date', end_date, '--initial-restart-dir',
        str(root / 'nwm/restarts/cnrfc/retro/2026/03'), '--archive-gate', str(gate),
    ]


def require_test(root):
    path = root / ('nwm/runs/cnrfc/nrt/nrt_warmstart_test_v1/'
                   '20260302-20260304/accepted.json')
    result = json.loads(path.read_text())
    expected = {'status': 'passed', 'passed': True, 'stream': 'nrt',
                'start': '2026-03-02 00:00:00', 'end': '2026-03-04 00:00:00',
                'hourly_channel_records': 48, 'daily_land_files': 2,
                'daily_channel_files': 2, 'daily_streamflow_oracle': 'first day passed'}
    if any(result.get(k) != value for k, value in expected.items()):
        raise ValueError('NRT warm-start acceptance is incomplete or failed')
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--end-date', required=True, help='Fixed endpoint at 00 UTC')
    parser.add_argument('--submit', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    first, end = simulation_window(2026, args.end_date, '2026-03-02')
    runs = root / 'nwm/runs/cnrfc/nrt/nrt_continuation_v1'
    runs.mkdir(parents=True, exist_ok=True)
    manifest = runs / 'initial-catchup-submission.json'
    with (runs / 'submission.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if manifest.exists():
            raise RuntimeError('Submission manifest exists; inspect before any resubmission')
        gate = require_test(root)
        for kind in ('outputs', 'restarts'):
            dest = root / f'nwm/{kind}/cnrfc/nrt'
            if any(p.name != 'tests' for p in dest.iterdir()):
                raise RuntimeError(f'Canonical NRT destination is not empty: {dest}')
        jobs = subprocess.check_output(['squeue', '--me', '-h', '-o', '%j'], text=True)
        if any('wrfh-cnrfc' in name for name in jobs.splitlines()):
            raise RuntimeError('Another CNRFC job is queued or running')
        initial = root / 'nwm/restarts/cnrfc/retro/2026/03'
        restart_check([initial / name for name in restart_names(first)], first)
        forcing = root / 'forcing/outputs/cnrfc/nrt/hourly'
        identities = {}
        for day in dates(first, end):
            path = forcing / day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
            identities[str(path)] = forcing_identity(path)
        terminal = forcing / end.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
        identities[str(terminal)] = forcing_identity(terminal)
        for name, identity in identities.items():
            audit = json.loads(Path(name + '.subset.json').read_text())
            if (audit.get('status') != 'passed' or audit.get('missing_active_values') != 0
                    or audit.get('output_identity') != identity):
                raise ValueError(f'Stale or failed forcing receipt: {name}')
        check_forcing(forcing, first, end)
        if any(forcing_identity(Path(p)) != identity for p, identity in identities.items()):
            raise RuntimeError('Forcing changed during preflight; retry')
        command = command_for(root, args.end_date)
        report = {'status': 'preflight_passed', 'start': str(first), 'end': str(end),
                  'acceptance_gate': str(gate), 'mpi_ranks': 64, 'command': command,
                  'forcing_identities': identities}
        if args.submit:
            report['status'] = 'submitting'
            write_json(manifest, report)
            try:
                result = subprocess.run(command, cwd=root, check=True, capture_output=True, text=True)
                report.update(status='submitted', job_id=int(result.stdout.strip().split(';')[0]))
            except BaseException as error:
                report.update(status='submission_uncertain', error=str(error))
                raise
            finally:
                write_json(manifest, report)
        print(json.dumps({k: v for k, v in report.items() if k != 'forcing_identities'}, indent=2))


if __name__ == '__main__':
    main()
