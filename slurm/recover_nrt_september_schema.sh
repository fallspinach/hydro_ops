#!/usr/bin/env bash
#SBATCH --partition=compute-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=12
#SBATCH --exclusive
#SBATCH --tmp=240000
#SBATCH --time=12:00:00
#SBATCH --array=0-1%2
#SBATCH --output=forcing/logs/nrt-schema-recovery-%A_%a.out
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
source "$root/bin/project_environment.sh"
cd "$root"
export HYDRO_OPS_MIN_SCRATCH_FREE_GB=120
case "${RECOVERY_STAGE:?}" in
baseline)
    export HYDRO_OPS_START_DAY=2026-09-01 HYDRO_OPS_ARCHIVE_DAILY=1
    export HYDRO_OPS_OUTPUT_ROOT="$root/forcing/outputs/conus/baseline/hourly"
    export HYDRO_OPS_LAYOUT_ROOT="$root"
    unset HYDRO_OPS_FORCING_DAY_TASK_FILE HYDRO_OPS_FORCE HYDRO_OPS_START_HOUR
    "$HYDRO_OPS_PYTHON" slurm/produce_forcing_day.py
    "$HYDRO_OPS_PYTHON" - <<'PY'
import os
from datetime import date, timedelta
from pathlib import Path
from hydro_ops.forcing.baseline_publication import accepted_baseline
d = date(2026,9,1)+timedelta(days=int(os.environ['SLURM_ARRAY_TASK_ID']))
p = Path(os.environ['HYDRO_OPS_OUTPUT_ROOT'])/d.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
if not accepted_baseline(p,d):
    raise RuntimeError(f'Baseline acceptance failed: {p}')
print(f'PASS baseline {d}', flush=True)
PY
    ;;
prism)
    unset HYDRO_OPS_RETRO_NEW_PRODUCTION HYDRO_OPS_REBUILD_STATIC_ENVELOPE
    export HYDRO_OPS_PRISM_CALENDAR_TASK_FILE="/scratch/${SLURM_JOB_USER}/job_${SLURM_JOB_ID}/tasks.jsonl"
    "$HYDRO_OPS_PYTHON" - <<'PY'
import json
import os
from pathlib import Path
root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
p = Path(os.environ['HYDRO_OPS_PRISM_CALENDAR_TASK_FILE'])
p.parent.mkdir(parents=True, exist_ok=True)
tasks = [dict(start=a, end=b, stream='nrt', revision='early',
              baseline_root=str(root/'forcing/outputs/conus/baseline/hourly'),
              output_root=str(root/'forcing/outputs/conus/nrt/hourly'), writer_profile='validated_chunks_v1')
         for a,b in [('2026-09-01','2026-09-07'),('2026-09-08','2026-09-13')]]
p.write_text(''.join(json.dumps(t)+'\n' for t in tasks))
PY
    "$HYDRO_OPS_PYTHON" slurm/produce_prism_calendar_batch.py
    ;;
*) echo 'Unknown recovery stage' >&2; exit 2;;
esac
