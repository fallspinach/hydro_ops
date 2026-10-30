#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=64
#SBATCH --tmp=120000
#SBATCH --time=24:00:00
#SBATCH --job-name=forcing-cnrfc-hu18-retro-1979-1980-subset-16workers
#SBATCH --output=forcing/logs/cnrfc-hu18-pilot-%j.out
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
source "$root/bin/project_environment.sh"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
exec "$HYDRO_OPS_PYTHON" "$root/bin/run_cnrfc_hu18_pilot.py" forcing --workers 16 "$@"
