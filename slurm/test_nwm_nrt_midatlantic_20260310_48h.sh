#!/usr/bin/env bash
#SBATCH --job-name=wrfh-midatlantic-nrt-20260310-48h-coldstart
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=4
#SBATCH --cpus-per-task=1
#SBATCH --tmp=120000
#SBATCH --time=01:00:00

set -euo pipefail

project_root=${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
python=/home/mpan/local/miniforge3/envs/hydro-ops/bin/python
subset="$project_root/work/nwm_subset_mid_atlantic"
template="$subset/run_prism_native_daily"
forcing_root="$project_root/outputs/forcing/nwm/nrt"
scratch_root=${SLURM_TMPDIR:-"/scratch/${SLURM_JOB_USER}/job_${SLURM_JOB_ID}"}
test_root="$scratch_root/wrfh-midatlantic-nrt-20260310-48h"
run_dir="$test_root/run"
result_dir="$project_root/outputs/wrf_hydro_tests/mid_atlantic/nrt_20260310_48h/job_${SLURM_JOB_ID}"

mkdir -p "$run_dir/forcing" "$result_dir"

"$python" "$project_root/bin/preflight_nwm_forcing.py" \
    "$forcing_root/2026/03/20260310.LDASIN_DOMAIN1" \
    "$forcing_root/2026/03/20260311.LDASIN_DOMAIN1" \
    "$forcing_root/2026/03/20260312.LDASIN_DOMAIN1" \
    --wrfinput "$subset/wrfinput_CONUS.nc" \
    --window 1956 2587 3807 4347 \
    --output-dir "$run_dir/forcing" \
    --max-fill-distance 25 \
    --report "$result_dir/forcing_coverage.json"

cp "$template/namelist.hrldas" "$run_dir/namelist.hrldas"
cp "$template/hydro.namelist" "$run_dir/hydro.namelist"
for table in CHANPARM.TBL GENPARM.TBL HYDRO.TBL MPTABLE.TBL SOILPARM.TBL; do
    cp "$template/$table" "$run_dir/$table"
done
ln -s "$subset" "$run_dir/DOMAIN"
ln -s "$project_root/external/wrf_hydro_nwm_public-v5.4.0/build-intel/Run/wrf_hydro_NoahMP.exe" \
    "$run_dir/wrf_hydro.exe"

sed -i \
    -e 's/^START_YEAR.*/START_YEAR  = 2026/' \
    -e 's/^START_MONTH.*/START_MONTH = 03/' \
    -e 's/^START_DAY.*/START_DAY   = 09/' \
    -e 's/^START_HOUR.*/START_HOUR  = 23/' \
    -e 's/^KHOUR.*/KHOUR = 48/' \
    "$run_dir/namelist.hrldas"
sed -i \
    -e 's/^[[:space:]]*RTOUT_DOMAIN[[:space:]]*=.*/RTOUT_DOMAIN = 0/' \
    -e 's/^[[:space:]]*t0OutputFlag[[:space:]]*=.*/t0OutputFlag = 0/' \
    -e '/^[[:space:]]*t0OutputFlag[[:space:]]*=/a\CHRTOUT_HOURLY = 0\nCHRTOUT_DAILY = 1\nLDASOUT_HOURLY = 0\nLDASOUT_DAILY = 1' \
    "$run_dir/hydro.namelist"

module purge
module load slurm/AWARE/23.02.7 cpu/0.21.2 intel/2023.2.4.31
module load intel-mpi/2021.14.2.9 netcdf-fortran/4.5.3

start=$SECONDS
(
    cd "$run_dir"
    mpiexec -n "$SLURM_NTASKS" ./wrf_hydro.exe > model.log 2>&1
)
grep -q "The model finished successfully" "$run_dir/model.log"

cp "$run_dir/model.log" "$result_dir/model.log"
find "$run_dir" -maxdepth 1 -type f -printf '%f %s\n' | sort > "$result_dir/output_inventory.txt"
cp "$run_dir"/*.daily "$result_dir/" 2>/dev/null || true
echo "elapsed_seconds=$((SECONDS - start))" | tee "$result_dir/summary.txt"
echo "forcing_stream=nrt" | tee -a "$result_dir/summary.txt"
echo "simulation_start=2026-03-09T23:00:00Z" | tee -a "$result_dir/summary.txt"
echo "simulation_hours=48" | tee -a "$result_dir/summary.txt"
echo "forcing_window=2026-03-10T00:00:00Z/2026-03-12T00:00:00Z" | tee -a "$result_dir/summary.txt"
echo "forcing_archive_granularity=utc_calendar_day" | tee -a "$result_dir/summary.txt"
echo "result_dir=$result_dir"
