"""Submit held promotion, validated synchronization/replay, and gated retirement."""
import fcntl
import json
import subprocess
from pathlib import Path

from hydro_ops.cnrfc_domain import migration_state
from hydro_ops.download.cdec_fnf import write_json


def main():
    root = Path(__file__).resolve().parents[1]
    directory = root / 'nwm/status/cnrfc-domain-migration'
    with (directory / 'submission.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = migration_state(root)
        if state.get('status') != 'preparing' or not state.get('blocked'):
            raise ValueError('Migration must be explicitly gated in preparing state')
        journal = directory / 'submission.json'
        if journal.exists():
            raise FileExistsError('Inspect existing migration submission before retrying')
        record = {'status': 'submitting', 'jobs': []}

        def submit(stage, script, arguments=(), dependency=None, extra=()):
            command = ['sbatch', '--parsable', f'--chdir={root}', '--partition=shared-128',
                       '--job-name=cnrfc-grid-migration-' + stage, *extra]
            if dependency:
                command += ['--dependency=afterok:' + dependency]
            command += [script, *arguments]
            entry = {'stage': stage, 'status': 'submission_uncertain', 'command': command}
            record['jobs'].append(entry)
            write_json(journal, record)
            job = subprocess.check_output(command, cwd=root, text=True).strip().split(';')[0]
            if not job.isdecimal():
                raise ValueError('Unexpected sbatch response')
            entry.update(status='submitted', job_id=job)
            write_json(journal, record)
            return job

        promotion = submit('promote', 'slurm/migrate_cnrfc_grid.sh',
                           ['--stage', 'promote'], extra=['--hold', '--time=04:00:00'])
        sync = submit('forcing-validation', 'slurm/sync_cnrfc_forcing.sh',
            ['--stream', 'nrt', '--frequency', 'all', '--migration-test'], promotion,
            ['--time=01:00:00', '--output=forcing/logs/cnrfc-promoted-sync-%j.out'])
        replay = submit('replay-validation', 'slurm/run_cnrfc_daily_cycle.sh',
            ['--test', '--migration-test', '--end-date', '2026-10-03', '--rewind-days', '1',
             '--snapshot-forcing'], sync)
        retirement = submit('retire-old-grid', 'slurm/migrate_cnrfc_grid.sh',
            ['--stage', 'retire', '--test-job', replay, '--sync-job', sync], replay)
        write_json(directory / 'pipeline-jobs.json', {
            'promotion': promotion, 'forcing_validation': sync, 'replay_validation': replay,
            'retirement': retirement})
        record['status'] = 'submitted_held_before_release'
        write_json(journal, record)
        subprocess.run(['scontrol', 'release', promotion], check=True)
        record['status'] = 'submitted'
        write_json(journal, record)
        print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
