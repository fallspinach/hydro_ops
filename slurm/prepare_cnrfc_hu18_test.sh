#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --tmp=120000
#SBATCH --time=12:00:00
#SBATCH --job-name=nwm-cnrfc-hu18-parameters-and-retro-nrt-forcing-pilot
#SBATCH --output=nwm/logs/cnrfc-hu18-prepare-%j.out
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
source "$root/bin/project_environment.sh"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
exec "$HYDRO_OPS_PYTHON" "$root/bin/prepare_cnrfc_hu18_test.py"
