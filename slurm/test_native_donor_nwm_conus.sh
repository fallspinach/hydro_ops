#!/usr/bin/env bash
# Midnight-to-midnight compatibility test after native repair and PRISM gates.
#SBATCH --job-name=nwm-conus-gfs-native-nldas-20260824-24h-120ranks
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=120
#SBATCH --time=04:00:00
#SBATCH --tmp=120000
set -euo pipefail
project_root=${HYDRO_OPS_PROJECT_ROOT:-${SLURM_SUBMIT_DIR:?}}
scratch="/scratch/${SLURM_JOB_USER:?}/job_${SLURM_JOB_ID:?}"
result_root="$project_root/nwm/outputs/tests/conus/native_donor_20260825/job_${SLURM_JOB_ID}"
pilot="$project_root/forcing/work/native-donor-pilot-20260825"
mkdir -p "$scratch/forcing/2026/08" "$scratch/run" "$result_root"
/home/mpan/local/miniforge3/envs/hydro-ops/bin/python - "$pilot" <<'PY'
import json
import sys
from pathlib import Path
p = Path(sys.argv[1]) / "nrt/2026/08/20260825.LDASIN_DOMAIN1.native-acceptance.json"
a = json.loads(p.read_text())
assert a["status"] == "passed" and len(a["hours"]) == 24
assert max(h["maximum_donor_km"] for h in a["hours"]) <= 40
PY
cp "$project_root/forcing/work/gfs-nrt-exploration/full_day/20260824.LDASIN_DOMAIN1" "$scratch/forcing/2026/08/"
cp "$pilot/nrt/2026/08/20260825.LDASIN_DOMAIN1" "$scratch/forcing/2026/08/"
cp "$pilot/nrt/2026/08/20260825.LDASIN_DOMAIN1.native-acceptance.json" "$result_root/forcing-acceptance.json"
export HYDRO_OPS_PROJECT_ROOT="$project_root" HYDRO_OPS_RUN_ROOT="$scratch/run"
export HYDRO_OPS_RESULT_ROOT="$result_root" HYDRO_OPS_FORCING_ROOT="$scratch/forcing"
export HYDRO_OPS_START_YEAR=2026 HYDRO_OPS_START_MONTH=08 HYDRO_OPS_START_DAY=24
export HYDRO_OPS_SIMULATION_HOURS=24 HYDRO_OPS_DTRT_TER=600 HYDRO_OPS_DTRT_CH=600
export HYDRO_OPS_WRFINPUT_NAME=wrfinput_CONUS_NLDAS2.nc
save_diagnostics() {
    for name in model.log namelist.hrldas hydro.namelist; do
        if [[ -f "$scratch/run/$name" ]]; then cp "$scratch/run/$name" "$result_root/"; fi
    done
}
trap save_diagnostics EXIT
bash "$project_root/slurm/benchmark_wrf_hydro_conus_spinup.sh"
test -s "$result_root/HYDRO_RST.2026-08-25_00:00_DOMAIN1"
/home/mpan/local/miniforge3/envs/hydro-ops/bin/python "$project_root/bin/audit_gfs_nwm_restart.py" \
    "$result_root/RESTART.2026082500_DOMAIN1" "$result_root/restart-audit.json" \
    --expected-time '2026-08-25_00:00:00'
