#!/usr/bin/env bash
# Run one restart-aware year of output-free CONUS WRF-Hydro spin-up.

#SBATCH --job-name=wrfh-conus-spinup-year
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=120
#SBATCH --time=48:00:00
#SBATCH --tmp=120000

set -euo pipefail

project_root=${HYDRO_OPS_PROJECT_ROOT:-${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}}
start_year=${HYDRO_OPS_START_YEAR:?missing HYDRO_OPS_START_YEAR}
cold_start=${HYDRO_OPS_COLD_START:-0}
ranks=${SLURM_NTASKS:?missing MPI task count}
job_id=${SLURM_JOB_ID:?this runner must execute under SLURM}
end_year=$((start_year + 1))

if (( start_year % 400 == 0 || (start_year % 4 == 0 && start_year % 100 != 0) )); then
    simulation_hours=8784
else
    simulation_hours=8760
fi
hydro_restart_minutes=$((simulation_hours * 60))

run_root="$project_root/nwm/runs/conus/retro/spinup/$start_year/job_$job_id"
log_root="$project_root/nwm/logs/wrf_hydro/conus/retro/spinup/$start_year/job_$job_id"
restart_root="$project_root/nwm/restarts/conus/retro"
destination_root="$restart_root/$end_year/01"
domain="$project_root/nwm/static/operational/nwm.v3.1.6/domain"
land_configuration="$project_root/nwm/static/operational/nwm.v3.1.6/analysis_assim"
hydro_configuration="$project_root/nwm/static/operational/nwm.v3.1.6/analysis_assim_no_lakes"
constants="$project_root/nwm/static/operational/nwm.v3.1.6/constants"
forcing="$project_root/forcing/outputs/conus/retro"
executable="$project_root/external/wrf_hydro_nwm_public-v5.4.0/build-intel/Run/wrf_hydro_NoahMP.exe"

input_land="$restart_root/$start_year/01/RESTART.${start_year}010100_DOMAIN1"
input_hydro="$restart_root/$start_year/01/HYDRO_RST.${start_year}-01-01_00:00_DOMAIN1"
output_land="RESTART.${end_year}010100_DOMAIN1"
output_hydro="HYDRO_RST.${end_year}-01-01_00:00_DOMAIN1"

if (( cold_start == 0 )); then
    [[ -s "$input_land" ]] || { echo "missing land restart: $input_land" >&2; exit 1; }
    [[ -s "$input_hydro" ]] || { echo "missing hydro restart: $input_hydro" >&2; exit 1; }
fi
[[ ! -e "$destination_root/$output_land" ]] || { echo "destination exists: $destination_root/$output_land" >&2; exit 1; }
[[ ! -e "$destination_root/$output_hydro" ]] || { echo "destination exists: $destination_root/$output_hydro" >&2; exit 1; }

mkdir -p "$run_root" "$log_root" "$destination_root"
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
    -e 's/^START_MONTH.*/START_MONTH = 01/' \
    -e 's/^START_DAY.*/START_DAY   = 01/' \
    -e 's/^START_HOUR.*/START_HOUR  = 00/' \
    -e "s/^KHOUR.*/KHOUR = $simulation_hours/" \
    -e "s|^HRLDAS_SETUP_FILE.*|HRLDAS_SETUP_FILE = './DOMAIN/wrfinput_CONUS_NLDAS2.nc'|" \
    -e "s/^RESTART_FREQUENCY_HOURS.*/RESTART_FREQUENCY_HOURS = $simulation_hours/" \
    -e 's/^FORC_TYP.*/FORC_TYP = 1/' \
    -e 's/^PCP_PARTITION_OPTION.*/PCP_PARTITION_OPTION = 1/' \
    "$run_root/namelist.hrldas"

sed -i \
    -e "s/^[[:space:]]*rst_dt[[:space:]]*=.*/rst_dt = $hydro_restart_minutes/" \
    -e 's/^[[:space:]]*rst_typ[[:space:]]*=.*/rst_typ = 0/' \
    -e 's/^[[:space:]]*t0OutputFlag[[:space:]]*=.*/t0OutputFlag = 0/' \
    -e 's/^[[:space:]]*CHRTOUT_DOMAIN[[:space:]]*=.*/CHRTOUT_DOMAIN = 0/' \
    -e 's/^[[:space:]]*CHANOBS_DOMAIN[[:space:]]*=.*/CHANOBS_DOMAIN = 0/' \
    -e 's/^[[:space:]]*CHRTOUT_GRID[[:space:]]*=.*/CHRTOUT_GRID = 0/' \
    -e 's/^[[:space:]]*LSMOUT_DOMAIN[[:space:]]*=.*/LSMOUT_DOMAIN = 0/' \
    -e 's/^[[:space:]]*RTOUT_DOMAIN[[:space:]]*=.*/RTOUT_DOMAIN = 0/' \
    -e 's/^[[:space:]]*output_gw[[:space:]]*=.*/output_gw = 0/' \
    -e 's/^[[:space:]]*outlake[[:space:]]*=.*/outlake = 0/' \
    -e 's/^[[:space:]]*frxst_pts_out[[:space:]]*=.*/frxst_pts_out = 0/' \
    -e 's/^[[:space:]]*DTRT_TER[[:space:]]*=.*/DTRT_TER = 600/' \
    -e 's/^[[:space:]]*DTRT_CH[[:space:]]*=.*/DTRT_CH = 600/' \
    -e "s|^[[:space:]]*diversions_file[[:space:]]*=.*|diversions_file = ''|" \
    "$run_root/hydro.namelist"
sed -i '/^[[:space:]]*t0OutputFlag[[:space:]]*=/a\CHRTOUT_HOURLY = 0\nCHRTOUT_DAILY = 0\nLDASOUT_HOURLY = 0\nLDASOUT_DAILY = 0' \
    "$run_root/hydro.namelist"

if (( cold_start == 1 )); then
    sed -i "s|^RESTART_FILENAME_REQUESTED.*|RESTART_FILENAME_REQUESTED = ' '|" \
        "$run_root/namelist.hrldas"
    sed -i \
        -e "s|^[[:space:]]*RESTART_FILE.*|! RESTART_FILE omitted for cold start|" \
        -e 's/^[[:space:]]*RSTRT_SWC[[:space:]]*=.*/RSTRT_SWC = 0/' \
        -e 's/^[[:space:]]*GW_RESTART[[:space:]]*=.*/GW_RESTART = 0/' \
        "$run_root/hydro.namelist"
else
    sed -i "s|^RESTART_FILENAME_REQUESTED.*|RESTART_FILENAME_REQUESTED = '$input_land'|" \
        "$run_root/namelist.hrldas"
    sed -i \
        -e "s|^[[:space:]]*RESTART_FILE.*|RESTART_FILE = '$input_hydro'|" \
        -e 's/^[[:space:]]*RSTRT_SWC[[:space:]]*=.*/RSTRT_SWC = 0/' \
        -e 's/^[[:space:]]*GW_RESTART[[:space:]]*=.*/GW_RESTART = 1/' \
        "$run_root/hydro.namelist"
fi

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
(( completion_count >= 1 )) || { echo "WRF-Hydro completion sentinel not found" >&2; exit 1; }
[[ -s "$run_root/$output_land" ]] || { echo "missing terminal land restart" >&2; exit 1; }
[[ -s "$run_root/$output_hydro" ]] || { echo "missing terminal hydro restart" >&2; exit 1; }
ncdump -h "$run_root/$output_land" >/dev/null
ncdump -h "$run_root/$output_hydro" >/dev/null

history_count=$(find "$run_root" -maxdepth 1 -type f \
    \( -name '*.LDASOUT_DOMAIN*' -o -name '*.CHRTOUT_DOMAIN*' -o -name '*.RTOUT_DOMAIN*' \
       -o -name '*.CHANOBS_DOMAIN*' -o -name '*.GWOUT_DOMAIN*' -o -name '*.LAKEOUT_DOMAIN*' \) \
    -print | wc -l)
(( history_count == 0 )) || { echo "unexpected history files: $history_count" >&2; exit 1; }

cp "$run_root/model.log" "$log_root/model.log"
cp "$run_root/namelist.hrldas" "$run_root/hydro.namelist" "$log_root/"
mv "$run_root/$output_land" "$run_root/$output_hydro" "$destination_root/"
find "$run_root" -maxdepth 1 -type f -name 'diag_hydro.*' -delete
cat > "$log_root/summary.txt" <<EOF
job_id=$job_id
start_year=$start_year
end_year=$end_year
cold_start=$cold_start
mpi_ranks=$ranks
started_utc=$started_utc
finished_utc=$finished_utc
elapsed_seconds=$elapsed_seconds
simulated_hours=$simulation_hours
dtrt_ter_seconds=600
dtrt_ch_seconds=600
completion_sentinels=$completion_count
history_files=$history_count
land_restart=$destination_root/$output_land
hydro_restart=$destination_root/$output_hydro
EOF

cat "$log_root/summary.txt"
