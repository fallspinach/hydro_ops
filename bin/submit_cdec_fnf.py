"""Submit one ordinary-resource CDEC FNF refresh; suppress duplicate jobs."""
import argparse
import fcntl
import json
import subprocess
from pathlib import Path

from hydro_ops.download.cdec_fnf import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    status = root / 'observations/status/cdec/fnf'
    status.mkdir(parents=True, exist_ok=True)
    with (status / 'submission.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        active = subprocess.check_output(['squeue', '--me', '--noheader', '--name=obs-cdec-fnf-refresh', '--format=%i'], text=True).strip()
        if active:
            print(json.dumps({'status': 'skipped', 'active_jobs': active.split()}))
            return
        command = ['sbatch', '--parsable', f'--chdir={root}', 'slurm/update_cdec_fnf.sh']
        if args.dry_run:
            print(json.dumps({'status': 'planned', 'command': command}))
            return
        journal = status / 'submission.json'
        if journal.exists() and json.loads(journal.read_text()).get('status') == 'submission_uncertain':
            raise RuntimeError('Inspect uncertain prior submission before retrying')
        write_json(journal, {'status': 'submission_uncertain', 'command': command})
        job = subprocess.check_output(command, cwd=root, text=True).strip().split(';')[0]
        if not job.isdecimal():
            raise ValueError(f'Unexpected sbatch response: {job}')
        write_json(journal, {'status': 'submitted', 'job_id': job, 'command': command})
        print(json.dumps({'status': 'submitted', 'job_id': job}))


if __name__ == '__main__':
    main()
