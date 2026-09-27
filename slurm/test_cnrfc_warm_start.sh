#!/usr/bin/env bash
#SBATCH --job-name=wrfh-cnrfc-conus-restart-subset-19790201-48h-32mpi
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=32
#SBATCH --cpus-per-task=1
#SBATCH --tmp=120000
#SBATCH --time=04:00:00
#SBATCH --output=nwm/logs/cnrfc-warm-test-%j.out
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
source "$root/bin/project_environment.sh"
module purge
module load slurm/AWARE/23.02.7 cpu/0.21.2 intel/2023.2.4.31
module load intel-mpi/2021.14.2.9 netcdf-fortran/4.5.3
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
exec "$HYDRO_OPS_PYTHON" "$root/bin/test_cnrfc_model.py" --warm-start "$@"
