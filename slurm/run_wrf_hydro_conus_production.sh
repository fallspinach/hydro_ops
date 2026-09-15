#!/usr/bin/env bash
#SBATCH --job-name=wrfh-conus-retro-monthly-checkpoints
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=120
#SBATCH --time=48:00:00
#SBATCH --tmp=240000
set -euo pipefail
project_root=${SLURM_SUBMIT_DIR:?}
module purge
module load slurm/AWARE/23.02.7 cpu/0.21.2 intel/2023.2.4.31
module load intel-mpi/2021.14.2.9 netcdf-fortran/4.5.3
export OMP_NUM_THREADS=1
export PYTHONPATH="$project_root/src${PYTHONPATH:+:$PYTHONPATH}"
exec /home/mpan/local/miniforge3/envs/hydro-ops/bin/python \
    -m hydro_ops.wrf_hydro.production --project "$project_root" "$@"
