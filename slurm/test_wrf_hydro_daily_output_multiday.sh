#!/usr/bin/env bash
#SBATCH --job-name=wrfh-native-daily-multiday-test
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=2
#SBATCH --cpus-per-task=1
#SBATCH --time=00:30:00

set -euo pipefail

project_root=${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
source_dir=${WRF_HYDRO_SOURCE_DIR:-"$project_root/external/wrf_hydro_nwm_public-v5.4.0"}
build_dir=${WRF_HYDRO_BUILD_DIR:-"$source_dir/build-intel"}
example_dir="$build_dir/Run/example_case"
scratch_root=${SLURM_TMPDIR:-"/scratch/${SLURM_JOB_USER}/job_${SLURM_JOB_ID}"}
test_root="$scratch_root/wrf_hydro_daily_output_multiday"
failure_log="$project_root/logs/wrf_hydro/native-daily-multiday-${SLURM_JOB_ID:-local}.model.log"
preserve_failure_log() {
    status=$?
    if [[ $status -ne 0 && -f "$test_root/model.log" ]]; then
        cp "$test_root/model.log" "$failure_log"
        echo "preserved_failure_log=$failure_log"
    fi
    exit "$status"
}
trap preserve_failure_log EXIT

module purge
module load slurm/AWARE/23.02.7 cpu/0.21.2 intel/2023.2.4.31
module load intel-mpi/2021.14.2.9 netcdf-fortran/4.5.3

mkdir -p "$test_root"
cp "$example_dir/NWM/namelist.hrldas" "$test_root/namelist.hrldas"
cp "$example_dir/NWM/hydro.namelist" "$test_root/hydro.namelist"
sed -i 's/^KDAY = 7/! KDAY = 7/; s/^! KHOUR = 8/KHOUR = 54/' "$test_root/namelist.hrldas"
sed -i '/^[[:space:]]*t0OutputFlag[[:space:]]*=/a\CHRTOUT_HOURLY = 0\nCHRTOUT_DAILY = 1\nLDASOUT_HOURLY = 0\nLDASOUT_DAILY = 1' "$test_root/hydro.namelist"

for item in DOMAIN RESTART nudgingTimeSliceObs; do
    ln -s "$example_dir/NWM/$item" "$test_root/$item"
done
ln -s "$example_dir/FORCING" "$test_root/FORCING"
ln -s "$build_dir/Run/wrf_hydro_NoahMP.exe" "$test_root/wrf_hydro.exe"
for table in CHANPARM.TBL GENPARM.TBL HYDRO.TBL MPTABLE.TBL SOILPARM.TBL; do
    ln -s "$build_dir/Run/$table" "$test_root/$table"
done

start=$SECONDS
(
    cd "$test_root"
    mpiexec -n "$SLURM_NTASKS" ./wrf_hydro.exe > model.log 2>&1
)
grep -q "The model finished successfully" "$test_root/model.log"

"/home/mpan/local/miniforge3/bin/conda" run --no-capture-output --name hydro-ops \
    python - "$test_root" <<'PY'
import sys
from pathlib import Path

import numpy as np
import xarray as xr

root = Path(sys.argv[1])
assert not list(root.glob("*.CHRTOUT_DOMAIN1"))
assert not list(root.glob("*.LDASOUT_DOMAIN1"))

expected = {
    "CHRTOUT": ["20110826", "20110827"],
    "LDASOUT": ["20110826", "20110827"],
}
for product, days in expected.items():
    files = sorted(root.glob(f"*.{product}_DOMAIN1.daily"))
    assert [path.name[:8] for path in files] == days, files
    for path in files:
        with xr.open_dataset(path) as dataset:
            bounds = dataset["time_bounds"].values.reshape(-1)
            assert bounds[1] - bounds[0] == np.timedelta64(24, "h")
            assert dataset.sizes["time"] == 1

assert not list(root.glob("20110828.*.daily")), "incomplete final day was published"
print("complete_daily_products=4")
print("incomplete_final_day_suppressed=true")
PY

echo "elapsed_seconds=$((SECONDS - start))"
echo "test_root=$test_root"
