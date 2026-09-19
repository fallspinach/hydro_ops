#!/usr/bin/env bash
# Isolated optimized-forcing calendar-boundary and restart-read smoke test.
#SBATCH --job-name=nwm-conus-nrt-reliability-24h-plus-6h-restart
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=120
#SBATCH --tmp=240000
#SBATCH --time=06:00:00
set -euo pipefail
project_root=${SLURM_SUBMIT_DIR:?}
campaign=${1:?pass accepted worker-sequence campaign}
scratch="/scratch/${SLURM_JOB_USER:?}/job_${SLURM_JOB_ID:?}"
result="$project_root/nwm/outputs/tests/conus/nrt_reliability/job_${SLURM_JOB_ID}"
python=/home/mpan/local/miniforge3/envs/hydro-ops/bin/python
"$python" -c 'import json,sys; assert json.load(open(sys.argv[1]))["status"] == "passed"' "$campaign/acceptance.json"
mkdir -p "$scratch/forcing/2026/09" "$result"
for day in 20260914 20260915; do
    cp "$campaign/nrt/2026/09/$day.LDASIN_DOMAIN1" "$scratch/forcing/2026/09/"
done
export HYDRO_OPS_PROJECT_ROOT="$project_root" HYDRO_OPS_RUN_ROOT="$scratch/run"
export HYDRO_OPS_RESULT_ROOT="$result/cold" HYDRO_OPS_FORCING_ROOT="$scratch/forcing"
export HYDRO_OPS_START_YEAR=2026 HYDRO_OPS_START_MONTH=09 HYDRO_OPS_START_DAY=14
export HYDRO_OPS_SIMULATION_HOURS=24 HYDRO_OPS_DTRT_TER=600 HYDRO_OPS_DTRT_CH=600
export HYDRO_OPS_WRFINPUT_NAME=wrfinput_CONUS_NLDAS2.nc
bash "$project_root/slurm/benchmark_wrf_hydro_conus_spinup.sh"
land="$result/cold/RESTART.2026091500_DOMAIN1"
hydro="$result/cold/HYDRO_RST.2026-09-15_00:00_DOMAIN1"
test -s "$land"
test -s "$hydro"
"$python" "$project_root/bin/audit_gfs_nwm_restart.py" "$land" "$result/cold/restart-audit.json"
# Resume at midnight from the pair actually produced above, without modifying it.
sed -i -e 's/^START_DAY.*/START_DAY = 15/' -e 's/^KHOUR.*/KHOUR = 6/' \
    -e 's/^RESTART_FREQUENCY_HOURS.*/RESTART_FREQUENCY_HOURS = 6/' \
    -e "s|^RESTART_FILENAME_REQUESTED.*|RESTART_FILENAME_REQUESTED = '$land'|" "$scratch/run/namelist.hrldas"
sed -i -e "s|^! RESTART_FILE omitted for cold start|RESTART_FILE = '$hydro'|" \
    -e 's/^[[:space:]]*rst_dt[[:space:]]*=.*/rst_dt = 360/' "$scratch/run/hydro.namelist"
module purge
module load slurm/AWARE/23.02.7 cpu/0.21.2 intel/2023.2.4.31 intel-mpi/2021.14.2.9 netcdf-fortran/4.5.3
(
    cd "$scratch/run"
    mpiexec -n "$SLURM_NTASKS" ./wrf_hydro.exe > restart-model.log 2>&1
)
cp "$scratch/run/restart-model.log" "$result/"
grep -q 'The model finished successfully' "$result/restart-model.log"
test -s "$scratch/run/HYDRO_RST.2026-09-15_06:00_DOMAIN1"
"$python" "$project_root/bin/audit_gfs_nwm_restart.py" \
    "$scratch/run/RESTART.2026091506_DOMAIN1" "$result/restart-audit.json"
cp "$scratch/run/namelist.hrldas" "$scratch/run/hydro.namelist" "$result/"
echo 'PASS: 24-hour midnight boundary and 6-hour restarted forcing read; no production state modified.'
