#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --tmp=120000
#SBATCH --time=04:00:00
#SBATCH --job-name=nwm-b120-union-buffer20km-masks-parameters
#SBATCH --output=nwm/logs/b120-domain-%j.out
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
source "$root/bin/project_environment.sh"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
base_python="$(dirname "$(dirname "$HYDRO_OPS_PYTHON")")/../../bin/python"
"$base_python" "$root/bin/prepare_b120_domain.py" geometry --source "${1:?shapefile required}"
exec "$HYDRO_OPS_PYTHON" "$root/bin/prepare_b120_domain.py" parameters
