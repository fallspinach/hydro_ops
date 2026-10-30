#!/usr/bin/env bash
#SBATCH --job-name=stage4-realtime-lookback-7vs10-abba-4cpu
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --tmp=20000
#SBATCH --time=02:00:00
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?}"
source bin/project_environment.sh
exec "$HYDRO_OPS_PYTHON" bin/benchmark_stage4_lookback.py
