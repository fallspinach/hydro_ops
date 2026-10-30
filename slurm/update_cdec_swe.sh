#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --time=04:00:00
#SBATCH --job-name=obs-cdec-swe-update
#SBATCH --output=observations/logs/cdec-swe-%A_%a.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
exec "$HYDRO_OPS_PYTHON" "$HYDRO_OPS_PROJECT_ROOT/bin/run_cdec_swe.py" "$@"
