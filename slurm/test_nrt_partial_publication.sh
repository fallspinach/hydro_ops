#!/usr/bin/env bash
#SBATCH --partition=compute-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=128
#SBATCH --tmp=240000
#SBATCH --time=03:00:00
#SBATCH --job-name=nrt-partial-day-extension-mixed-gfs-noop-guard
#SBATCH --output=forcing/logs/nrt-partial-publication-%j.out
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
source "$root/bin/project_environment.sh"
cd "$root"
export HYDRO_OPS_MIN_SCRATCH_FREE_GB=120
exec "$HYDRO_OPS_PYTHON" bin/test_nrt_partial_publication.py
