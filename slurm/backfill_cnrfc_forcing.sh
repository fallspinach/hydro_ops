#!/usr/bin/env bash
#SBATCH --job-name=forcing-cnrfc-retro-nrt-hourly-daily-monthly-backfill-16workers
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=64
#SBATCH --tmp=120000
#SBATCH --time=48:00:00
#SBATCH --output=forcing/logs/cnrfc-forcing-backfill-%j.out
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
source "$root/bin/project_environment.sh"
cd "$root"
"$HYDRO_OPS_PYTHON" bin/backfill_cnrfc_forcing.py --workers 16 \
    --scratch "/scratch/${SLURM_JOB_USER:?}/job_${SLURM_JOB_ID:?}"
