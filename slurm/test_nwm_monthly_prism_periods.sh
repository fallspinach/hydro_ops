#!/usr/bin/env bash
#SBATCH --job-name=wrfh-monthly-prism-retro-checks
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=4
#SBATCH --cpus-per-task=1
#SBATCH --tmp=120000
#SBATCH --time=02:00:00

set -euo pipefail

project_root=${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
python=/home/mpan/local/miniforge3/envs/hydro-ops/bin/python
subset="$project_root/work/nwm_subset_mid_atlantic"
template="$subset/run_prism_native_daily"
forcing_root="$project_root/outputs/forcing/nwm/retro"
scratch_root=${SLURM_TMPDIR:-"/scratch/${SLURM_JOB_USER}/job_${SLURM_JOB_ID}"}

case ${SLURM_ARRAY_TASK_ID} in
  0) label=partial_19790101; sy=1979; sm=01; sd=01; sh=12; khour=12; files=(1979/01/19790101.LDASIN_DOMAIN1 1979/01/19790102.LDASIN_DOMAIN1) ;;
  1) label=leap_19800228_48h; sy=1980; sm=02; sd=28; sh=00; khour=48; files=(1980/02/19800228.LDASIN_DOMAIN1 1980/02/19800229.LDASIN_DOMAIN1 1980/03/19800301.LDASIN_DOMAIN1) ;;
  2) label=warm_19800715_24h; sy=1980; sm=07; sd=15; sh=00; khour=24; files=(1980/07/19800715.LDASIN_DOMAIN1 1980/07/19800716.LDASIN_DOMAIN1) ;;
  *) exit 2 ;;
esac

run_dir="$scratch_root/$label/run"
result_dir="$project_root/outputs/wrf_hydro_tests/mid_atlantic/monthly_prism/$label/job_${SLURM_JOB_ID}"
mkdir -p "$run_dir/forcing" "$result_dir"
trap 'test -f "$run_dir/model.log" && cp "$run_dir/model.log" "$result_dir/model.log" || true' EXIT
inputs=()
for item in "${files[@]}"; do inputs+=("$forcing_root/$item"); done
"$python" "$project_root/bin/preflight_nwm_forcing.py" "${inputs[@]}" \
  --wrfinput "$subset/wrfinput_CONUS.nc" --window 1956 2587 3807 4347 \
  --output-dir "$run_dir/forcing" --max-fill-distance 25 \
  --report "$result_dir/forcing_coverage.json"

cp "$template/namelist.hrldas" "$run_dir/namelist.hrldas"
cp "$template/hydro.namelist" "$run_dir/hydro.namelist"
for table in CHANPARM.TBL GENPARM.TBL HYDRO.TBL MPTABLE.TBL SOILPARM.TBL; do cp "$template/$table" "$run_dir/$table"; done
ln -s "$subset" "$run_dir/DOMAIN"
ln -s "$project_root/external/wrf_hydro_nwm_public-v5.4.0/build-intel/Run/wrf_hydro_NoahMP.exe" "$run_dir/wrf_hydro.exe"
sed -i -e "s/^START_YEAR.*/START_YEAR = $sy/" -e "s/^START_MONTH.*/START_MONTH = $sm/" \
  -e "s/^START_DAY.*/START_DAY = $sd/" -e "s/^START_HOUR.*/START_HOUR = $sh/" \
  -e "s/^KHOUR.*/KHOUR = $khour/" "$run_dir/namelist.hrldas"
sed -i -e 's/^[[:space:]]*RTOUT_DOMAIN[[:space:]]*=.*/RTOUT_DOMAIN = 0/' \
  -e 's/^[[:space:]]*t0OutputFlag[[:space:]]*=.*/t0OutputFlag = 0/' "$run_dir/hydro.namelist"

module purge
module load slurm/AWARE/23.02.7 cpu/0.21.2 intel/2023.2.4.31
module load intel-mpi/2021.14.2.9 netcdf-fortran/4.5.3
start=$SECONDS
(cd "$run_dir" && mpiexec -n "$SLURM_NTASKS" ./wrf_hydro.exe > model.log 2>&1)
grep -q 'The model finished successfully' "$run_dir/model.log"
cp "$run_dir/model.log" "$result_dir/model.log"
find "$run_dir" -maxdepth 1 -type f -printf '%f %s\n' | sort > "$result_dir/output_inventory.txt"
printf 'label=%s\nelapsed_seconds=%s\nstart=%04d-%02d-%02dT%02d:00:00Z\nhours=%d\n' \
  "$label" "$((SECONDS-start))" "$sy" "$sm" "$sd" "$sh" "$khour" > "$result_dir/summary.txt"
echo "$result_dir"
