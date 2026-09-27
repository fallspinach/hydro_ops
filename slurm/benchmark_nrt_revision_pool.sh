#!/usr/bin/env bash
#SBATCH --job-name=nrt-revision-seven-days-serial-vs-2baseline-workers
#SBATCH --partition=compute-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=128
#SBATCH --tmp=240000
#SBATCH --time=12:00:00
#SBATCH --output=forcing/logs/nrt-revision-pool-%j.out
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
source "$root/bin/project_environment.sh"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
exec "$HYDRO_OPS_PYTHON" "$root/bin/benchmark_nrt_revision_pool.py" \
    --start 2026-09-18 --end 2026-09-24 \
    --campaign "$root/forcing/work/nrt-revision-pool/job_${SLURM_JOB_ID}"
