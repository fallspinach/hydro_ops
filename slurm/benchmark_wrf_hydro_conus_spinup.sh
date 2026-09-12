#!/usr/bin/env bash
# Benchmark an output-free CONUS WRF-Hydro cold start.

#SBATCH --job-name=wrfh-conus-spinup30d
#SBATCH --partition=shared-128
#SBATCH --time=12:00:00
#SBATCH --tmp=120000

set -euo pipefail

project_root=${HYDRO_OPS_PROJECT_ROOT:-${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}}
ranks=${SLURM_NTASKS:?submit with an explicit MPI task count}
nodes=${SLURM_JOB_NUM_NODES:?missing SLURM node count}
job_id=${SLURM_JOB_ID:?this benchmark must run under SLURM}
start_year=${HYDRO_OPS_START_YEAR:-1981}
start_month=${HYDRO_OPS_START_MONTH:-01}
start_day=${HYDRO_OPS_START_DAY:-01}
simulation_hours=${HYDRO_OPS_SIMULATION_HOURS:-720}
dtrt_ter=${HYDRO_OPS_DTRT_TER:-10}
dtrt_ch=${HYDRO_OPS_DTRT_CH:-300}
wrfinput_name=${HYDRO_OPS_WRFINPUT_NAME:-wrfinput_CONUS.nc}
hydro_restart_minutes=$((simulation_hours * 60))
run_root=${HYDRO_OPS_RUN_ROOT:-"$project_root/nwm/runs/conus_spinup_benchmark/job_${job_id}_${ranks}ranks"}
result_root=${HYDRO_OPS_RESULT_ROOT:-"$project_root/nwm/outputs/tests/conus/spinup_30d/job_${job_id}_${ranks}ranks"}
domain="$project_root/nwm/static/operational/nwm.v3.1.6/domain"
land_configuration="$project_root/nwm/static/operational/nwm.v3.1.6/analysis_assim"
hydro_configuration="$project_root/nwm/static/operational/nwm.v3.1.6/analysis_assim_no_lakes"
constants="$project_root/nwm/static/operational/nwm.v3.1.6/constants"
forcing=${HYDRO_OPS_FORCING_ROOT:-"$project_root/forcing/outputs/conus/retro"}
executable="$project_root/external/wrf_hydro_nwm_public-v5.4.0/build-intel/Run/wrf_hydro_NoahMP.exe"

mkdir -p "$run_root" "$result_root"
cp "$land_configuration/namelist.hrldas" "$run_root/namelist.hrldas"
cp "$hydro_configuration/hydro.namelist" "$run_root/hydro.namelist"
for table in CHANPARM.TBL GENPARM.TBL HYDRO.TBL MPTABLE.TBL SOILPARM.TBL; do
    cp "$constants/$table" "$run_root/$table"
done
ln -s "$domain" "$run_root/DOMAIN"
ln -s "$executable" "$run_root/wrf_hydro.exe"

sed -i \
    -e "s|^INDIR *=.*|INDIR = '$forcing'|" \
    -e "s/^START_YEAR.*/START_YEAR  = $start_year/" \
    -e "s/^START_MONTH.*/START_MONTH = $start_month/" \
    -e "s/^START_DAY.*/START_DAY   = $start_day/" \
    -e 's/^START_HOUR.*/START_HOUR  = 00/' \
    -e "s/^KHOUR.*/KHOUR = $simulation_hours/" \
    -e "s|^HRLDAS_SETUP_FILE.*|HRLDAS_SETUP_FILE = './DOMAIN/$wrfinput_name'|" \
    -e "s|^RESTART_FILENAME_REQUESTED.*|RESTART_FILENAME_REQUESTED = ' '|" \
    -e "s/^RESTART_FREQUENCY_HOURS.*/RESTART_FREQUENCY_HOURS = $simulation_hours/" \
    -e 's/^FORC_TYP.*/FORC_TYP = 1/' \
    -e 's/^PCP_PARTITION_OPTION.*/PCP_PARTITION_OPTION = 1/' \
    "$run_root/namelist.hrldas"

sed -i \
    -e "s|^[[:space:]]*RESTART_FILE.*|! RESTART_FILE omitted for cold start|" \
    -e "s/^[[:space:]]*rst_dt[[:space:]]*=.*/rst_dt = $hydro_restart_minutes/" \
    -e 's/^[[:space:]]*rst_typ[[:space:]]*=.*/rst_typ = 0/' \
    -e 's/^[[:space:]]*RSTRT_SWC[[:space:]]*=.*/RSTRT_SWC = 0/' \
    -e 's/^[[:space:]]*GW_RESTART[[:space:]]*=.*/GW_RESTART = 0/' \
    -e 's/^[[:space:]]*t0OutputFlag[[:space:]]*=.*/t0OutputFlag = 0/' \
    -e 's/^[[:space:]]*CHRTOUT_DOMAIN[[:space:]]*=.*/CHRTOUT_DOMAIN = 0/' \
    -e 's/^[[:space:]]*CHANOBS_DOMAIN[[:space:]]*=.*/CHANOBS_DOMAIN = 0/' \
    -e 's/^[[:space:]]*CHRTOUT_GRID[[:space:]]*=.*/CHRTOUT_GRID = 0/' \
    -e 's/^[[:space:]]*LSMOUT_DOMAIN[[:space:]]*=.*/LSMOUT_DOMAIN = 0/' \
    -e 's/^[[:space:]]*RTOUT_DOMAIN[[:space:]]*=.*/RTOUT_DOMAIN = 0/' \
    -e 's/^[[:space:]]*output_gw[[:space:]]*=.*/output_gw = 0/' \
    -e 's/^[[:space:]]*outlake[[:space:]]*=.*/outlake = 0/' \
    -e 's/^[[:space:]]*frxst_pts_out[[:space:]]*=.*/frxst_pts_out = 0/' \
    -e "s/^[[:space:]]*DTRT_TER[[:space:]]*=.*/DTRT_TER = $dtrt_ter/" \
    -e "s/^[[:space:]]*DTRT_CH[[:space:]]*=.*/DTRT_CH = $dtrt_ch/" \
    -e "s|^[[:space:]]*diversions_file[[:space:]]*=.*|diversions_file = ''|" \
    "$run_root/hydro.namelist"
sed -i '/^[[:space:]]*t0OutputFlag[[:space:]]*=/a\CHRTOUT_HOURLY = 0\nCHRTOUT_DAILY = 0\nLDASOUT_HOURLY = 0\nLDASOUT_DAILY = 0' \
    "$run_root/hydro.namelist"

module purge
module load slurm/AWARE/23.02.7 cpu/0.21.2 intel/2023.2.4.31
module load intel-mpi/2021.14.2.9 netcdf-fortran/4.5.3

started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
start_seconds=$SECONDS
(
    cd "$run_root"
    mpiexec -n "$ranks" ./wrf_hydro.exe > model.log 2>&1
)
elapsed_seconds=$((SECONDS - start_seconds))
finished_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)

completion_count=$(grep -c 'The model finished successfully' "$run_root/model.log" || true)
if (( completion_count < 1 )); then
    echo "WRF-Hydro completion sentinel not found" >&2
    exit 1
fi

mapfile -t land_restarts < <(find "$run_root" -maxdepth 1 -type f -name 'RESTART.*_DOMAIN1' -print)
mapfile -t hydro_restarts < <(find "$run_root" -maxdepth 1 -type f -name 'HYDRO_RST.*_DOMAIN1' -print)
if (( ${#land_restarts[@]} != 1 || ${#hydro_restarts[@]} != 1 )); then
    echo "expected one terminal land/hydro restart pair; found ${#land_restarts[@]}/${#hydro_restarts[@]}" >&2
    exit 1
fi

history_count=$(find "$run_root" -maxdepth 1 -type f \
    \( -name '*.LDASOUT_DOMAIN*' -o -name '*.CHRTOUT_DOMAIN*' -o -name '*.RTOUT_DOMAIN*' \
       -o -name '*.CHANOBS_DOMAIN*' -o -name '*.GWOUT_DOMAIN*' -o -name '*.LAKEOUT_DOMAIN*' \) \
    -print | wc -l)
if (( history_count != 0 )); then
    echo "history output was not fully disabled: $history_count files" >&2
    exit 1
fi

cp "$run_root/model.log" "$result_root/model.log"
find "$run_root" -maxdepth 1 -type f -printf '%f %s\n' | sort > "$result_root/run_inventory.txt"
mv "${land_restarts[0]}" "${hydro_restarts[0]}" "$result_root/"
scontrol show hostnames "$SLURM_JOB_NODELIST" > "$result_root/nodes.txt"
cat > "$result_root/summary.txt" <<EOF
job_id=$job_id
mpi_ranks=$ranks
nodes=$nodes
started_utc=$started_utc
finished_utc=$finished_utc
elapsed_seconds=$elapsed_seconds
simulated_hours=$simulation_hours
dtrt_ter_seconds=$dtrt_ter
dtrt_ch_seconds=$dtrt_ch
simulated_days=$(awk -v hours="$simulation_hours" 'BEGIN { printf "%.6f", hours / 24 }')
simulated_days_per_wall_day=$(awk -v seconds="$elapsed_seconds" -v hours="$simulation_hours" 'BEGIN { printf "%.6f", hours * 3600 / seconds }')
projected_365_day_seconds=$(awk -v seconds="$elapsed_seconds" -v hours="$simulation_hours" 'BEGIN { printf "%.0f", seconds * 8760 / hours }')
wrfinput=$wrfinput_name
completion_sentinels=$completion_count
history_files=$history_count
land_restart=$(basename "${land_restarts[0]}")
hydro_restart=$(basename "${hydro_restarts[0]}")
EOF

echo "benchmark complete: $result_root"
cat "$result_root/summary.txt"
