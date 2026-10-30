#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=64
#SBATCH --tmp=120000
#SBATCH --time=08:00:00
#SBATCH --output=forcing/logs/subdomain-nrt-catchup-%j.out
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
source "$root/bin/project_environment.sh"
exec "$HYDRO_OPS_PYTHON" "$root/bin/sync_subdomain_nrt_forcing.py" "$@"
