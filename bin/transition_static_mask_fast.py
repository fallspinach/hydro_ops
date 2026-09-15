"""One-time authorized transition of campaign 4519428; retain running tasks."""
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    old = root/'forcing/work/static-envelope-v4-1979-2002-20260912'
    pending = subprocess.check_output(['squeue', '-r', '-h', '-j', '4519428', '-t', 'PENDING', '-o', '%i'], text=True)
    indices = sorted(int(line.split('_')[1]) for line in pending.splitlines())
    if not indices:
        raise ValueError('No pending tasks to transition')
    campaign = root/'forcing/work'/('static-envelope-v4-fast-'+datetime.now(UTC).strftime('%Y%m%dT%H%M%S'))
    campaign.mkdir(exist_ok=False)
    config = json.loads((old/'campaign.json').read_text())
    config.update(fast=True, writers=2, audit_root=str(old/'audit'), predecessor='4519428')
    (campaign/'campaign.json').write_text(json.dumps(config, indent=2)+'\n')
    shutil.copyfile(old/'tasks.jsonl', campaign/'tasks.jsonl')
    record = {'pending_snapshot': indices, 'campaign': str(campaign), 'status': 'prepared'}
    journal = campaign/'transition.json'
    journal.write_text(json.dumps(record, indent=2)+'\n')
    # State filter preserves any task that starts between snapshot and cancel.
    # afterany below also waits for those raced tasks; their files then resume/skip.
    subprocess.run(['scancel', '--state=PENDING', '4519428'], check=True)
    record['status'] = 'pending_cancelled'
    journal.write_text(json.dumps(record, indent=2)+'\n')
    env = os.environ.copy()
    env.update(STATIC_ENVELOPE_CAMPAIGN=str(campaign), OMP_NUM_THREADS='1',
               OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1')
    def submit(array, dependency, label):
        cmd = ['sbatch', '--parsable', '--partition=shared-128', '--nodes=1', '--ntasks=1',
               '--cpus-per-task=12', '--tmp=120000', '--time=48:00:00',
               '--array='+array, '--dependency='+dependency,
               '--job-name=nwm-retro-static-v4-fast-2writers-'+label,
               '--output='+str(root/'forcing/logs/static-envelope-fast-%A_%a.out'),
               '--wrap', f'{sys.executable} {root}/slurm/apply_static_forcing_mask.py']
        return subprocess.check_output(cmd, text=True, env=env).strip().split(';')[0]
    record['canary'] = submit(str(indices[0]), 'afterany:4519428', 'canary')
    journal.write_text(json.dumps(record, indent=2)+'\n')
    if len(indices) > 1:
        record['remainder'] = submit(','.join(map(str, indices[1:]))+'%8',
                                     'afterok:'+record['canary'], '1979-2002-remainder')
    record['status'] = 'submitted'
    journal.write_text(json.dumps(record, indent=2)+'\n')
    print(json.dumps({k: v for k, v in record.items() if k != 'pending_snapshot'}, indent=2))
    print('Replacement batches:', len(indices))


if __name__ == '__main__':
    main()
