#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=120
#SBATCH --time=01:00:00
#SBATCH --tmp=240000
#SBATCH --job-name=wrfh-conus-hourly-layout-19860101-read-smoke
#SBATCH --output=nwm/logs/wrf_hydro/layout-cutover-%j.out
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
source "$root/bin/project_environment.sh"
cd "$root"
module purge
module load slurm/AWARE/23.02.7 cpu/0.21.2 intel/2023.2.4.31
module load intel-mpi/2021.14.2.9 netcdf-fortran/4.5.3
export OMP_NUM_THREADS=1
"$HYDRO_OPS_PYTHON" - <<'PY'
import json
import os
from datetime import datetime
from pathlib import Path
from hydro_ops.wrf_hydro.production import run_segment
from hydro_ops.forcing.retro_publication import check_scratch
root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{os.environ["SLURM_JOB_ID"]}')
check_scratch(scratch)
receipt = root/'nwm/runs/conus/retro/production_1979_v1/1985-passed.json'
inputs = [Path(p) for p in json.loads(receipt.read_text())['restarts']]
logs = root/'nwm/runs/tests'/f'hourly-layout-{os.environ["SLURM_JOB_ID"]}'
run_segment(root, scratch/'model', logs, scratch/'outputs', scratch/'restarts',
            inputs, datetime(1986,1,1), datetime(1986,1,2), int(os.environ['SLURM_NTASKS']))
print('PASS migrated hourly forcing read: 24 CONUS hours; scratch-only model products', flush=True)
PY
