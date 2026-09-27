#!/usr/bin/env bash
#SBATCH --job-name=nrt-incremental-staged-4-4-2-vs-serial-and-repeat
#SBATCH --partition=compute-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=128
#SBATCH --tmp=300000
#SBATCH --time=12:00:00
#SBATCH --output=forcing/logs/nrt-incremental-staged-%j.out
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
source "$root/bin/project_environment.sh"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
exec "$HYDRO_OPS_PYTHON" "$root/bin/test_nrt_incremental_staged.py"
