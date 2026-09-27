#!/usr/bin/env bash
#SBATCH --job-name=nrt-revision-staged-4baseline-4prism-2publish
#SBATCH --partition=compute-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=128
#SBATCH --tmp=300000
#SBATCH --time=12:00:00
#SBATCH --output=forcing/logs/nrt-staged-revision-%j.out
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
source "$root/bin/project_environment.sh"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
exec "$HYDRO_OPS_PYTHON" "$root/bin/benchmark_nrt_staged_revision.py" \
    --campaign "$root/forcing/work/nrt-staged-revision/job_${SLURM_JOB_ID}"
