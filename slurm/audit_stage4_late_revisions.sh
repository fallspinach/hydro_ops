#!/usr/bin/env bash
#SBATCH --job-name=stage4-day8to10-content-revision-audit
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --tmp=20000
#SBATCH --time=01:00:00
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?}"
source bin/project_environment.sh
exec "$HYDRO_OPS_PYTHON" bin/audit_stage4_late_revisions.py "$@"
