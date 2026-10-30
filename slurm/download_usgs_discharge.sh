#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --time=02:00:00
#SBATCH --job-name=obs-usgs-discharge
#SBATCH --output=observations/logs/usgs-discharge-%A_%a.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
exec "$HYDRO_OPS_PYTHON" "$HYDRO_OPS_PROJECT_ROOT/bin/run_usgs_discharge.py" "$@"
