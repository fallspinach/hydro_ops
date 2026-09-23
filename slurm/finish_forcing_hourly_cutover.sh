#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --time=02:00:00
#SBATCH --job-name=forcing-hourly-cutover-final-audit-release-1986
#SBATCH --output=forcing/logs/hourly-cutover-final-%j.out
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
source "$root/bin/project_environment.sh"
cd "$root"
"$HYDRO_OPS_PYTHON" - <<'PY'
import json
import subprocess
import time
from pathlib import Path
from hydro_ops.forcing.gfs_publication import _atomic_json
import os
root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
state = root/'forcing/status/layout-migration/cutover-20260923'
deadline = time.monotonic() + 90*60
while not (state/'acceptance.json').exists():
    if time.monotonic() >= deadline:
        raise RuntimeError('Migration completion timed out; NWM 1986 remains held')
    time.sleep(30)
migration = json.loads((state/'acceptance.json').read_text())
if migration['status'] != 'passed':
    raise RuntimeError('Migration not accepted')
nrt = json.loads((root/'forcing/status/layout-migration/operational-gate-4627946/acceptance.json').read_text())
if (nrt['status'] != 'passed' or not nrt['file_identities_unchanged']
        or any(d['status'] != 'unchanged' for phase in ('repair', 'noop') for d in nrt[phase]['days'])):
    raise RuntimeError('Post-migration NRT no-op failed; keep model held')
model = json.loads((root/'nwm/runs/tests/hourly-layout-4627947/accepted.json').read_text())
if model.get('passed') is not True or model['hourly_records'] != 24 or model['daily_records'] != 1:
    raise RuntimeError('Post-migration CONUS test failed; keep model held')
subprocess.run([os.environ['HYDRO_OPS_PYTHON'], str(root/'bin/verify_forcing_hourly_layout.py'),
                '--report', str(state/'final-read-verification.json')], check=True)
job = subprocess.check_output(['scontrol', 'show', 'job', '4524571', '-o'], text=True)
if ('JobState=PENDING' not in job or 'Reason=JobHeldUser' not in job
        or 'JobName=wrfh-conus-retro-1986-monthly-checkpoints' not in job):
    raise RuntimeError('Expected 1986 held job changed; do not release anything')
_atomic_json(state/'release-gate.json', {'status': 'passed', 'job_to_release': '4524571',
    'migration': migration, 'nrt_noop_job': '4627946', 'model_test_job': '4627947',
    'release_status': 'ready'})
subprocess.run(['scontrol', 'release', '4524571'], check=True)
_atomic_json(state/'release-gate.json', {'status': 'passed', 'job_released': '4524571',
    'migration': migration, 'nrt_noop_job': '4627946', 'model_test_job': '4627947',
    'release_status': 'released'})
print('PASS cutover acceptance; released NWM 1986 job 4524571', flush=True)
PY
