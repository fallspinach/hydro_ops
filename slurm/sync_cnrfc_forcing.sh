#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --tmp=120000
#SBATCH --time=12:00:00
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
source "$root/bin/project_environment.sh"
cd "$root"
exec "$HYDRO_OPS_PYTHON" bin/backfill_cnrfc_forcing.py --incremental --workers 8 \
    --scratch "/scratch/${SLURM_JOB_USER:?}/job_${SLURM_JOB_ID:?}" "$@"
