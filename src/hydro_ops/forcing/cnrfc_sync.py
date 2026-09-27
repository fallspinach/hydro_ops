"""Schedule serialized, restartable CNRFC forcing propagation jobs."""
import fcntl
import json
import re
import subprocess
from pathlib import Path

from hydro_ops.forcing.gfs_publication import _atomic_json


def command(root, stream, frequency, partition, account, dependencies):
    if stream not in {'all', 'nrt', 'retro'} or frequency not in {'all', 'hourly', 'daily', 'monthly'}:
        raise ValueError('Unsupported CNRFC synchronization selection')
    args = ['sbatch', '--parsable', f'--partition={partition}',
            f'--job-name=forcing-cnrfc-sync-{stream}-{frequency}',
            f'--chdir={root}', f'--output={root}/forcing/logs/cnrfc-sync-%j.out']
    if account:
        args.append(f'--account={account}')
    if dependencies:
        if not all(re.fullmatch(r'\d+', str(d)) for d in dependencies):
            raise ValueError('Invalid dependency job ID')
        # afterany allows a later sweep to repair missed work after a failed cycle.
        args.append('--dependency=afterany:' + ':'.join(sorted(set(map(str, dependencies)))))
    return args + [str(root / 'slurm/sync_cnrfc_forcing.sh'), '--stream', stream, '--frequency', frequency]


def submit(root: Path, *, stream='nrt', frequency='all', after=None, partition=None, account=None):
    from hydro_ops.config import load_settings
    settings = load_settings()
    partition = partition or settings.slurm_partition
    account = settings.slurm_account if account is None else account
    state = root / 'forcing/status/cnrfc'
    state.mkdir(parents=True, exist_ok=True)
    with (state / '.sync-submit.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        queued = subprocess.check_output(['squeue', '--me', '--noheader', '--format=%i|%j'], text=True)
        dependencies = [str(after)] if after else []
        for line in queued.splitlines():
            job, name = line.strip().split('|', 1)
            if (name.startswith(('forcing-cnrfc-sync-', 'forcing-cnrfc-retro-nrt-hourly-daily-monthly-backfill'))
                    and re.fullmatch(r'\d+', job)):
                dependencies.append(job)
        args = command(root, stream, frequency, partition, account, dependencies)
        job = subprocess.check_output(args, cwd=root, text=True).strip().split(';')[0]
        if not re.fullmatch(r'\d+', job):
            raise RuntimeError(f'Unexpected sbatch response: {job}')
        record = {'status': 'submitted', 'job_id': job, 'stream': stream,
                  'frequency': frequency, 'dependencies': dependencies, 'command': args}
        _atomic_json(state / f'submission-{job}.json', record)
        print(json.dumps(record), flush=True)
        return job
