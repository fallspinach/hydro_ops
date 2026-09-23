#!/usr/bin/env bash
#SBATCH --partition=compute-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=12
#SBATCH --exclusive
#SBATCH --tmp=240000
#SBATCH --time=06:00:00
#SBATCH --job-name=nrt-schema-v1-sep15-mixed-baselines-isolated-prism
#SBATCH --output=forcing/logs/nrt-schema-v1-%j.out
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
source "$root/bin/project_environment.sh"
cd "$root"
export HYDRO_OPS_MIN_SCRATCH_FREE_GB=120
unset HYDRO_OPS_RETRO_NEW_PRODUCTION HYDRO_OPS_REBUILD_STATIC_ENVELOPE
export SLURM_ARRAY_TASK_ID=0
export HYDRO_OPS_PRISM_CALENDAR_TASK_FILE="/scratch/${SLURM_JOB_USER}/job_${SLURM_JOB_ID}/schema-test.jsonl"
"$HYDRO_OPS_PYTHON" - <<'PY'
import json
import os
from pathlib import Path
root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
p = Path(os.environ['HYDRO_OPS_PRISM_CALENDAR_TASK_FILE'])
p.parent.mkdir(parents=True, exist_ok=True)
task = dict(start='2026-09-15', end='2026-09-15', stream='nrt', revision='early',
            baseline_root=str(root/'forcing/outputs/conus/baseline/hourly'),
            output_root=str(root/'forcing/work'/f'nrt-schema-v1-{os.environ["SLURM_JOB_ID"]}'/'output'),
            writer_profile='validated_chunks_v1')
p.write_text(json.dumps(task)+'\n')
print(json.dumps(task), flush=True)
PY
"$HYDRO_OPS_PYTHON" slurm/produce_prism_calendar_batch.py
