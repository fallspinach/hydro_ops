#!/usr/bin/env bash
#SBATCH --partition=compute-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=12
#SBATCH --exclusive
#SBATCH --tmp=240000
#SBATCH --time=06:00:00
#SBATCH --job-name=nrt-sep15-schema-production-repair-and-noop-gate
#SBATCH --output=forcing/logs/nrt-schema-operational-%j.out
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
source "$root/bin/project_environment.sh"
cd "$root"
export HYDRO_OPS_MIN_SCRATCH_FREE_GB=120
"$HYDRO_OPS_PYTHON" - <<'PY'
import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from hydro_ops.forcing.nrt_cycle import activation, day_path, identity, run_cycle
from hydro_ops.forcing.gfs_publication import _atomic_json
from hydro_ops.forcing.retro_publication import check_scratch

root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
work = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{os.environ["SLURM_JOB_ID"]}')
work.mkdir(parents=True, exist_ok=True)
check_scratch(work)
if not activation(root):
    raise RuntimeError('Operational NRT activation is missing')
day = date(2026, 9, 15)
state = root/'forcing/status/layout-migration'/f'operational-gate-{os.environ["SLURM_JOB_ID"]}'
state.mkdir(parents=True, exist_ok=True)
# Use the normal operational state/lock, production paths and source selection.
# No external refresh runs between these two cycles.
first = run_cycle(root, work, day, day, datetime.now(UTC))
_atomic_json(state/'repair.json', first)
if first['status'] != 'passed':
    raise RuntimeError('Repair failed; migration remains blocked')
paths = [day_path(root/'forcing/outputs/conus/baseline', day+timedelta(days=i)) for i in (-1, 0, 1)]
paths += [day_path(root/'forcing/outputs/conus/nrt', date.fromisoformat(r['day'])) for r in first['days']]
before = [identity(p) for p in paths]
second = run_cycle(root, work, day, day, datetime.now(UTC))
_atomic_json(state/'noop.json', second)
unchanged = before == [identity(p) for p in paths]
passed = second['status'] == 'passed' and all(r['status'] == 'unchanged' for r in second['days']) and unchanged
report = {'status': 'passed' if passed else 'failed', 'file_identities_unchanged': unchanged,
          'paths': [str(p) for p in paths], 'repair': first, 'noop': second}
_atomic_json(state/'acceptance.json', report)
print(json.dumps(report), flush=True)
if not passed:
    raise RuntimeError('No-op test failed; migration remains blocked')
PY
