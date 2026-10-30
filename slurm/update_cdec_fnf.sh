#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --time=02:00:00
#SBATCH --job-name=obs-cdec-fnf-refresh
#SBATCH --output=observations/logs/cdec-fnf-%j.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
"$HYDRO_OPS_PYTHON" "$HYDRO_OPS_PROJECT_ROOT/bin/update_cdec_fnf.py" --workers 2
exec "$HYDRO_OPS_PYTHON" "$HYDRO_OPS_PROJECT_ROOT/bin/audit_cdec_fnf.py" \
    --run-report "$HYDRO_OPS_PROJECT_ROOT/observations/status/cdec/fnf/job-${SLURM_JOB_ID}/status.json"
