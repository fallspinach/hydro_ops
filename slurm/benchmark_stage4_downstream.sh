#!/usr/bin/env bash
#SBATCH --job-name=stage4-10day-marginal-nrt-revision-3days-128cpu
#SBATCH --partition=compute-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=128
#SBATCH --tmp=300000
#SBATCH --time=03:00:00
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?}"
source bin/project_environment.sh
exec "$HYDRO_OPS_PYTHON" bin/benchmark_stage4_downstream.py
