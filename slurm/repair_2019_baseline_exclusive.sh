#!/usr/bin/env bash
#SBATCH --partition=compute-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=12
#SBATCH --exclusive
#SBATCH --tmp=240000
#SBATCH --time=04:00:00
#SBATCH --output=forcing/logs/baseline-exclusive-%A_%a.out
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
source "$root/bin/project_environment.sh"
cd "$root"
days=(2019-01-06 2019-01-07 2019-01-08 2019-01-11 2019-01-13 2019-01-14 2019-03-19 2019-03-20 2019-03-21 2019-03-22)
day=${days[${SLURM_ARRAY_TASK_ID:?}]}
export HYDRO_OPS_START_DAY="$day"
unset HYDRO_OPS_FORCING_DAY_TASK_FILE HYDRO_OPS_FORCE HYDRO_OPS_START_HOUR
export HYDRO_OPS_OUTPUT_ROOT="$root/forcing/outputs/conus/baseline"
export HYDRO_OPS_LAYOUT_ROOT="$root"
export HYDRO_OPS_ARCHIVE_DAILY=1 HYDRO_OPS_MIN_SCRATCH_FREE_GB=120
export HYDRO_OPS_ASSEMBLY_WORKERS=4 HYDRO_OPS_PRECIPITATION_REMAP_WORKERS=1
echo "START day=$day node=$(hostname) utc=$(date -u +%FT%TZ)"
monitor_scratch() {
    while true; do
        date -u +%FT%TZ
        df -B1 /scratch
        df -i /scratch
        sleep 30
    done
}
monitor_scratch &
monitor_pid=$!
trap 'kill "$monitor_pid" 2>/dev/null || true' EXIT
# The date above is explicit; prevent the array index from shifting it again.
SLURM_ARRAY_TASK_ID=0 "$HYDRO_OPS_PYTHON" slurm/produce_forcing_day.py
"$HYDRO_OPS_PYTHON" - "$day" <<'PY'
import json
import os
import runpy
import sys
from datetime import date
from pathlib import Path
from hydro_ops.forcing.daily_archive import verified_daily_archive

day = date.fromisoformat(sys.argv[1])
path = Path(os.environ['HYDRO_OPS_OUTPUT_ROOT']) / day.strftime('%Y/%m') / f'{day:%Y%m%d}.LDASIN_DOMAIN1'
domain_repaired = runpy.run_path('slurm/produce_forcing_day.py')['domain_repaired']
if not verified_daily_archive(path, day) or not domain_repaired(path, day):
    raise RuntimeError(f'Baseline publication acceptance failed: {path}')
print(json.dumps({'status': 'passed', 'day': str(day), 'path': str(path)}), flush=True)
PY
df -B1 /scratch
