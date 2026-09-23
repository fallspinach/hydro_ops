#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --time=02:00:00
#SBATCH --job-name=retro-2019-20201013-final-recovery-audit
#SBATCH --output=forcing/logs/retro-2019-recovery-audit-%j.out
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
source "$root/bin/project_environment.sh"
cd "$root"
"$HYDRO_OPS_PYTHON" - <<'PY'
import json
import os
import runpy
from datetime import UTC, date, datetime
from pathlib import Path
from netCDF4 import Dataset
root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
helpers = runpy.run_path(str(root/'bin/run_retro_forcing_block.py'))
with Dataset(root/'forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc') as ds:
    mask_hash = str(ds.keep_sha256)
count = helpers['audit'](root, date(2019,1,1), date(2020,10,13), mask_hash)
report = dict(status='passed', start='2019-01-01', end='2020-10-13', days=count,
              mask_sha256=mask_hash, job_id=os.environ['SLURM_JOB_ID'],
              created=datetime.now(UTC).isoformat(),
              cleanup='deferred; baseline and boundary days retained',
              original_controller='4524980', repair_array=os.environ['REPAIR_ARRAY'])
path = root/'forcing/work/retro-2003-20201013-gated-20260914/block-20190101-20201013.accepted.json'
if path.exists():
    raise FileExistsError(path)
helpers['write'](path, report)
print(json.dumps(report), flush=True)
PY
