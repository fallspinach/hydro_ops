"""Submit bounded daily Stage-IV observations; no production changes."""
import argparse
import fcntl
import json
import subprocess
from datetime import UTC, date, datetime

from hydro_ops.config import load_settings
from hydro_ops.forcing.gfs_publication import _atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--until', type=date.fromisoformat, default=date(2026, 11, 30))
    args = parser.parse_args()
    today = datetime.now(UTC).date()
    if today > args.until:
        print('Stage-IV observation campaign ended; review results before extending.')
        return
    root = load_settings().project_root
    state = root / 'forcing/status/stage4-revisions'
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'submission.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        active = subprocess.check_output(['squeue', '--me', '-h', '-o', '%j'], text=True)
        if 'stage4-day8to10-content-revision-audit' in active.splitlines():
            print('SKIP active Stage-IV observation audit')
            return
        command = ['sbatch', '--parsable', f'--chdir={root}',
                   f'--output={root}/forcing/logs/stage4-revision-audit-%j.out',
                   str(root / 'slurm/audit_stage4_late_revisions.sh'), '--monitor']
        job = subprocess.check_output(command, cwd=root, text=True).strip().split(';')[0]
        record = {'job_id': job, 'submitted_at_utc': datetime.now(UTC).isoformat(),
                  'until': str(args.until), 'command': command}
        _atomic_json(state / f'submission-{job}.json', record)
        print(json.dumps(record))


if __name__ == '__main__':
    main()
