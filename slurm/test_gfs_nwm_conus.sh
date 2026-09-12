#!/usr/bin/env bash
# Isolated 23-hour CONUS forcing-read/routing acceptance test, not a skill test.
# End at 23 UTC to avoid the separate August 25 primary-coverage issue.
#SBATCH --job-name=nwm-conus-gfs-nrt-20260824-23h-120ranks
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=120
#SBATCH --time=04:00:00
#SBATCH --tmp=120000

set -euo pipefail
project_root=${HYDRO_OPS_PROJECT_ROOT:-${SLURM_SUBMIT_DIR:?}}
scratch="/scratch/${SLURM_JOB_USER:?}/job_${SLURM_JOB_ID:?}"
result_root="$project_root/nwm/outputs/tests/conus/gfs_nrt_20260824/job_${SLURM_JOB_ID}"
mkdir -p "$scratch/forcing/2026/08" "$scratch/run" "$result_root"

# Keep model I/O on node scratch; never modify the tested forcing or real restarts.
for day in 20260824; do
    source="$project_root/forcing/work/gfs-nrt-exploration/full_day/$day.LDASIN_DOMAIN1"
    /home/mpan/local/miniforge3/envs/hydro-ops/bin/python - "$source" <<'PY'
import json
import sys
from pathlib import Path
from netCDF4 import Dataset
path = Path(sys.argv[1])
audit = json.loads(path.with_name(path.name + ".gfs-audit.json").read_text())
if (audit["status"] != "passed" or audit["missing_active_values"] != 0
        or audit["valid_outside_envelope"] != 0):
    raise RuntimeError(f"Forcing audit not accepted: {path}")
with Dataset(path) as data:
    if data.forcing_source != "hrrr_gfs_nrt" or len(data.dimensions["time"]) != 24:
        raise RuntimeError(f"Unexpected forcing bundle: {path}")
print(f"Accepted {path}", flush=True)
PY
    cp "$source" "$scratch/forcing/2026/08/"
    cp "$source.gfs-audit.json" "$result_root/$day.forcing-audit.json"
done

export HYDRO_OPS_PROJECT_ROOT="$project_root"
export HYDRO_OPS_RUN_ROOT="$scratch/run"
export HYDRO_OPS_RESULT_ROOT="$result_root"
export HYDRO_OPS_FORCING_ROOT="$scratch/forcing"
export HYDRO_OPS_START_YEAR=2026 HYDRO_OPS_START_MONTH=08 HYDRO_OPS_START_DAY=24
export HYDRO_OPS_SIMULATION_HOURS=23 HYDRO_OPS_DTRT_TER=600 HYDRO_OPS_DTRT_CH=600
export HYDRO_OPS_WRFINPUT_NAME=wrfinput_CONUS_NLDAS2.nc
save_diagnostics() {
    for name in model.log namelist.hrldas hydro.namelist; do
        if [[ -f "$scratch/run/$name" ]]; then cp "$scratch/run/$name" "$result_root/"; fi
    done
}
trap save_diagnostics EXIT
bash "$project_root/slurm/benchmark_wrf_hydro_conus_spinup.sh"
# Non-midnight restart files are test artifacts only, never operational restarts.
test -s "$result_root/RESTART.2026082423_DOMAIN1"
test -s "$result_root/HYDRO_RST.2026-08-24_23:00_DOMAIN1"
/home/mpan/local/miniforge3/envs/hydro-ops/bin/python "$project_root/bin/audit_gfs_nwm_restart.py" \
    "$result_root/RESTART.2026082423_DOMAIN1" "$result_root/restart-audit.json"
echo "GFS NRT CONUS 23-hour acceptance passed: $result_root"
