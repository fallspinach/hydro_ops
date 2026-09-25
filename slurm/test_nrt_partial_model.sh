#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=120
#SBATCH --tmp=240000
#SBATCH --time=03:00:00
#SBATCH --job-name=nwm-conus-partial-nrt-15h-reader-cold-smoke
#SBATCH --output=nwm/logs/nrt-partial-model-%j.out
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
source "$root/bin/project_environment.sh"
scratch="/scratch/${SLURM_JOB_USER:?}/job_${SLURM_JOB_ID:?}"
campaign="$root/forcing/work/nrt-partial-publication-4631499"
"$HYDRO_OPS_PYTHON" -c 'import json,sys; assert json.load(open(sys.argv[1]))["status"] == "passed"' "$campaign/acceptance.json"
mkdir -p "$scratch/forcing/2026/09"
cp "$campaign/nrt/2026/09/20260920.LDASIN_DOMAIN1" "$scratch/forcing/2026/09/"
export HYDRO_OPS_PROJECT_ROOT="$root" HYDRO_OPS_RUN_ROOT="$scratch/run"
export HYDRO_OPS_RESULT_ROOT="$root/nwm/outputs/tests/conus/nrt_partial/job_${SLURM_JOB_ID}"
export HYDRO_OPS_FORCING_ROOT="$scratch/forcing"
export HYDRO_OPS_START_YEAR=2026 HYDRO_OPS_START_MONTH=09 HYDRO_OPS_START_DAY=20
export HYDRO_OPS_SIMULATION_HOURS=15 HYDRO_OPS_DTRT_TER=600 HYDRO_OPS_DTRT_CH=600
export HYDRO_OPS_WRFINPUT_NAME=wrfinput_CONUS_NLDAS2.nc
bash "$root/slurm/benchmark_wrf_hydro_conus_spinup.sh"
"$HYDRO_OPS_PYTHON" "$root/bin/audit_gfs_nwm_restart.py" \
    "$HYDRO_OPS_RESULT_ROOT/RESTART.2026092015_DOMAIN1" \
    "$HYDRO_OPS_RESULT_ROOT/restart-audit.json" --expected-time 2026-09-20_15:00:00
