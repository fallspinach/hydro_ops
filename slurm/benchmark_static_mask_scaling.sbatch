#!/bin/bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --array=0-7%8
#SBATCH --cpus-per-task=12
#SBATCH --tmp=120000
#SBATCH --time=04:00:00
#SBATCH --job-name=nwm-static-mask-scaling-8tasks-2writers-64days
#SBATCH --output=forcing/logs/static-mask-scaling-%A_%a.out
set -euo pipefail
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
/home/mpan/local/miniforge3/envs/hydro-ops/bin/python slurm/benchmark_static_mask_scaling.py "${MASK_SCALING_PHASE:-build}"
