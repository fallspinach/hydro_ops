#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --time=02:00:00
#SBATCH --job-name=forcing-conus-nrt-daily-summary-and-repeat-20260920
#SBATCH --output=forcing/logs/nrt-summary-%j.out
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
source "$root/bin/project_environment.sh"
cd "$root"
for trial in first repeat; do
    echo "$trial"
    "$HYDRO_OPS_PYTHON" bin/backfill_forcing_summaries.py \
        --stream nrt --input-root "$root/forcing/outputs/conus/nrt/hourly" \
        --output-root "$root/forcing/work/nrt-summary-test-${SLURM_JOB_ID}" \
        --start 2026-09-20 --end 2026-09-20 --complete-months-only --workers 1
done
