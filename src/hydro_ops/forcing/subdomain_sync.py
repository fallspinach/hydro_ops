"""Daily NRT forcing branches downstream of CONUS summaries and CNRFC crops."""
import fcntl
import json
import re
import subprocess
from pathlib import Path

from hydro_ops.forcing.gfs_publication import _atomic_json

PARENTS = {'b120': 'cnrfc', 'ucrb': 'conus', 'yampa': 'ucrb'}


def job_id(value):
    value = str(value)
    if not re.fullmatch(r'\d+', value):
        raise ValueError(f'Invalid dependency job ID: {value}')
    return value


def submit_domain(root, domain, parent_job, partition, account=''):
    if domain not in PARENTS:
        raise ValueError('Unsupported daily subset domain')
    parent_job = job_id(parent_job)
    state = root / 'forcing/status' / domain
    state.mkdir(parents=True, exist_ok=True)
    with (state / '.sync-submit.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        queue = subprocess.check_output(['squeue', '--me', '-h', '-o', '%i|%j'], text=True)
        previous = set()
        for line in queue.splitlines():
            number, name = line.strip().split('|', 1)
            if name.startswith((f'forcing-{domain}-', f'{domain}-forcing-')):
                previous.add(job_id(number))
        previous.discard(parent_job)
        dependency = 'afterok:' + parent_job
        if previous:
            dependency += ',afterany:' + ':'.join(sorted(previous))
        command = ['sbatch', '--parsable', f'--partition={partition}',
                   f'--chdir={root}', f'--job-name=forcing-{domain}-daily-nrt-sync-all',
                   '--cpus-per-task=32', '--kill-on-invalid-dep=yes', '--dependency=' + dependency]
        if account:
            command.append('--account=' + account)
        command += [str(root / 'slurm/sync_subdomain_nrt_forcing.sh'),
                    '--domain', domain, '--workers', '8']
        number = job_id(subprocess.check_output(command, cwd=root, text=True).strip().split(';')[0])
        _atomic_json(state / f'submission-{number}.json', {
            'status': 'submitted', 'job_id': number, 'domain': domain,
            'source_domain': PARENTS[domain], 'stream': 'nrt', 'frequency': 'all',
            'parent_job': parent_job, 'serialized_after': sorted(previous), 'command': command})
        return number


def submit_daily_branches(root: Path, *, cnrfc_job, summary_job, cycle_id,
                          partition, account=''):
    """Persist each submission, allowing safe retries of a partially submitted chain."""
    cnrfc_job, summary_job, cycle_id = map(job_id, (cnrfc_job, summary_job, cycle_id))
    state = root / 'forcing/status/subdomain-sync'
    state.mkdir(parents=True, exist_ok=True)
    path = state / f'daily-{cycle_id}.json'
    with (state / '.submit.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        inputs = {'cnrfc_job': cnrfc_job, 'summary_job': summary_job}
        record = json.loads(path.read_text()) if path.exists() else {
            'status': 'submitting', 'cycle_id': cycle_id, 'inputs': inputs, 'jobs': {}}
        if record['inputs'] != inputs:
            raise ValueError('Existing daily chain has different parent jobs')
        if record.get('submission_uncertain'):
            raise RuntimeError('Inspect uncertain submission before retrying daily chain')
        for domain in ('b120', 'ucrb', 'yampa'):
            if domain in record['jobs']:
                continue
            parent = {'b120': cnrfc_job, 'ucrb': summary_job,
                      'yampa': record['jobs'].get('ucrb')}.get(domain)
            record['submission_uncertain'] = domain
            _atomic_json(path, record)
            record['jobs'][domain] = submit_domain(root, domain, parent, partition, account)
            record.pop('submission_uncertain')
            _atomic_json(path, record)
        record['status'] = 'submitted'
        _atomic_json(path, record)
        return record['jobs']
