#!/usr/bin/env bash
#SBATCH --partition=compute-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=12
#SBATCH --exclusive
#SBATCH --tmp=240000
#SBATCH --time=12:00:00
#SBATCH --array=0-1%2
#SBATCH --job-name=retro-prism-2019-jan-mar-17day-repair
#SBATCH --output=forcing/logs/prism-2019-gap-repair-%A_%a.out
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
source "$root/bin/project_environment.sh"
cd "$root"
export HYDRO_OPS_RETRO_NEW_PRODUCTION=1 HYDRO_OPS_REBUILD_STATIC_ENVELOPE=1
export HYDRO_OPS_MIN_SCRATCH_FREE_GB=120
export HYDRO_OPS_PRISM_CALENDAR_TASK_FILE="/scratch/${SLURM_JOB_USER}/job_${SLURM_JOB_ID}/prism-tasks.jsonl"
"$HYDRO_OPS_PYTHON" - <<'PY'
import json
import os
from pathlib import Path
root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
path = Path(os.environ['HYDRO_OPS_PRISM_CALENDAR_TASK_FILE'])
path.parent.mkdir(parents=True, exist_ok=True)
tasks = [dict(start=a, end=b, stream='retro', revision='stable',
              baseline_root=str(root/'forcing/outputs/conus/baseline'),
              output_root=str(root/'forcing/outputs/conus/retro'),
              writer_profile='validated_chunks_v1')
         for a, b in [('2019-01-05', '2019-01-15'), ('2019-03-18', '2019-03-23')]]
path.write_text(''.join(json.dumps(t)+'\n' for t in tasks))
PY
exec "$HYDRO_OPS_PYTHON" slurm/produce_prism_calendar_batch.py
