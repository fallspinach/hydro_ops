#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --time=02:00:00
#SBATCH --job-name=obs-usgs-conus-daily-discharge-inventory-four-domains
#SBATCH --output=observations/logs/usgs-streamflow-inventory-%j.out
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
source "$root/bin/project_environment.sh"
# The existing site base environment supplies GeoPandas/GDAL for domain boundaries.
base_python="$(dirname "$(dirname "$HYDRO_OPS_PYTHON")")/../../bin/python"
exec "$base_python" "$root/bin/collect_usgs_streamflow_inventory.py" "$@"
