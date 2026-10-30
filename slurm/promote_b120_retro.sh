#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --time=04:00:00
#SBATCH --job-name=wrfh-b120-retro-promotion-audit
#SBATCH --output=nwm/logs/b120-retro-promotion-%j.out
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
source "$root/bin/project_environment.sh"
exec "$HYDRO_OPS_PYTHON" "$root/bin/promote_yampa_nrt.py" --domain b120 --stream retro --execute
