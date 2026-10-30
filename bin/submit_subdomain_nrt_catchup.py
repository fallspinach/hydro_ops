"""Submit forcing refreshes, option-1 handoff gates, and bounded NRT catch-up runs."""
# Model time boundaries use naive UTC.
# ruff: noqa: DTZ007
import argparse
import fcntl
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from netCDF4 import Dataset, num2date
from prepare_cnrfc_hu18_test import approved as approved_cnrfc
from run_b120_production import approved as approved_b120
from run_cnrfc_production import forcing_identity
from run_yampa_production import approved as approved_yampa

from hydro_ops.wrf_hydro.production import restart_names, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--end-date', help='Default: newest CONUS NRT collection midnight')
    parser.add_argument('--domain', choices=('b120',), help='Submit a separate B120 continuation campaign')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    state = root / 'nwm/status/subdomain-nrt-catchup'
    if args.domain:
        state = root / 'nwm/status' / (args.domain + '-nrt-catchup')
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'submission.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal = state / 'submission.json'
        if journal.exists():
            raise FileExistsError('Existing catch-up journal; inspect before any retry')
        active = subprocess.check_output(['squeue', '--me', '-h', '-o', '%j'], text=True)
        if any('subdomain-nrt' in line for line in active.splitlines()):
            raise RuntimeError('Another subdomain NRT catch-up is active')
        parent = root / 'forcing/outputs' / ('cnrfc' if args.domain == 'b120' else 'conus') / 'nrt/hourly'
        latest = max(parent.glob('*/*/????????.LDASIN_DOMAIN1'))
        end = datetime.strptime(args.end_date, '%Y-%m-%d') if args.end_date else datetime.strptime(latest.name[:8], '%Y%m%d')
        terminal = parent / end.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
        with Dataset(terminal) as ds:
            stamps = num2date(ds['time'][:], ds['time'].units)
            if not any((t.year, t.month, t.day, t.hour) == (end.year, end.month, end.day, 0) for t in stamps):
                raise ValueError('Required model endpoint forcing hour is absent')
        plan = {'authorized': True, 'end': end.isoformat(), 'domains': {},
                'operational_cron_changed': False, 'restart_frequency': 'monthly plus terminal'}
        specs = (
            ('cnrfc_hu18_v1', 'hu18_pilot_v1', 'nwm/status/cnrfc_hu18_v1/hu18_pilot_v1/job-4956455/acceptance.json'),
            ('yampa', 'production_v1', 'nwm/status/yampa/production_v1/job-5050817/acceptance.json'),
        ) if not args.domain else (('b120', 'production_v1', 'nwm/status/b120/production_v1/job-5037306/acceptance.json'),)
        for domain, campaign, gate in specs:
            {'cnrfc_hu18_v1': approved_cnrfc, 'yampa': approved_yampa, 'b120': approved_b120}[domain](root)
            result = json.loads((root / 'nwm/runs' / domain / 'retro' / campaign / '2026-passed.json').read_text())
            if not result.get('passed') or result.get('stream') != 'retro':
                raise ValueError('Missing passed retro predecessor')
            start = datetime.fromisoformat(result['end'])
            if not start.year == end.year or not start + timedelta(days=2) <= end:
                raise ValueError('Require a bounded same-year NRT continuation with a two-day test')
            initial = root / 'nwm/restarts' / domain / 'retro' / campaign / start.strftime('%Y/%m')
            planned_pair = [str(initial / n) for n in restart_names(start)]
            if result['restarts'] != planned_pair:
                raise ValueError('Predecessor restart pair mismatch')
            for kind in ('outputs', 'restarts'):
                dest = root / 'nwm' / kind / domain / 'nrt/nrt_continuation_v1'
                if dest.exists():
                    raise FileExistsError('Inspect existing NRT campaign before resubmission')
            day = start
            while day <= end:
                if not (parent / day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')).is_file():
                    raise ValueError(f'Missing CONUS source day: {day}')
                day += timedelta(days=1)
            prior_gate = json.loads((root / gate).read_text())
            if prior_gate.get('status') != 'passed' or prior_gate.get('domain') != domain:
                raise ValueError('Invalid domain-specific inherited gate')
            plan['domains'][domain] = {'start': start.isoformat(), 'retro_campaign': campaign,
                'retro_gate': gate, 'mask_sha256': prior_gate['mask_sha256'],
                'initial_restart_dir': str(initial.relative_to(root)),
                'initial_restart_identities': {n: forcing_identity(initial / n) for n in restart_names(start)}}
        record = {'status': 'submitting', 'end': str(end), 'jobs': []}
        write_json(journal, record)

        def submit(label, script, arguments, dependencies=(), ranks=None):
            command = ['sbatch', '--parsable', f'--chdir={root}', '--job-name=' + label]
            if ranks:
                command += [f'--ntasks={ranks}']
            if dependencies:
                command += ['--dependency=afterok:' + ':'.join(dependencies)]
            command += [script, *arguments]
            entry = {'status': 'submission_uncertain', 'command': command, 'label': label}
            record['jobs'].append(entry)
            write_json(journal, record)
            job = subprocess.check_output(command, cwd=root, text=True).strip().split(';')[0]
            if not job.isdecimal():
                raise ValueError('Unexpected sbatch response')
            entry.update(status='submitted', job_id=job)
            write_json(journal, record)
            return job

        forcing_jobs = {}
        for domain in (('b120',) if args.domain else ('cnrfc_hu18_v1', 'ucrb', 'yampa')):
            deps = [forcing_jobs['ucrb']] if domain == 'yampa' else []
            forcing_jobs[domain] = submit('forcing-' + domain + '-subdomain-nrt-refresh',
                'slurm/sync_subdomain_nrt_forcing.sh', ['--domain', domain, '--end-date', end.strftime('%Y-%m-%d')], deps)
            if domain in plan['domains']:
                plan['domains'][domain]['forcing_report'] = str(Path('forcing/status') / domain / (
                    'nrt-catchup-' + forcing_jobs[domain]) / 'status.json')
        plan_file = state / 'plan.json'
        write_json(plan_file, plan)
        for domain, ranks in ((('b120', 32),) if args.domain else (('cnrfc_hu18_v1', 64), ('yampa', 32))):
            arguments = ['--plan', str(plan_file), '--domain', domain]
            gate = submit('wrfh-' + domain + '-subdomain-nrt-handoff-gate',
                'slurm/run_subdomain_nrt_catchup.sh', arguments + ['--stage', 'gate'],
                [forcing_jobs[domain]], ranks)
            submit('wrfh-' + domain + '-subdomain-nrt-apr18-' + end.strftime('%b%d').lower(),
                'slurm/run_subdomain_nrt_catchup.sh', arguments + ['--stage', 'production'], [gate], ranks)
        record.update(status='submitted', plan=str(plan_file.relative_to(root)))
        write_json(journal, record)
        print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
