#!/usr/bin/env bash
#SBATCH --job-name=wrfh-native-daily-restart-test
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
test_root="$scratch_root/wrf_hydro_daily_output_restart"

module purge
module load slurm/AWARE/23.02.7 cpu/0.21.2 intel/2023.2.4.31
module load intel-mpi/2021.14.2.9 netcdf-fortran/4.5.3

prepare_run() {
    local run_dir=$1
    mkdir -p "$run_dir"
    cp "$example_dir/NWM/namelist.hrldas" "$run_dir/namelist.hrldas"
    cp "$example_dir/NWM/hydro.namelist" "$run_dir/hydro.namelist"
    for item in DOMAIN nudgingTimeSliceObs; do
        ln -s "$example_dir/NWM/$item" "$run_dir/$item"
    done
    ln -s "$example_dir/FORCING" "$run_dir/FORCING"
    ln -s "$build_dir/Run/wrf_hydro_NoahMP.exe" "$run_dir/wrf_hydro.exe"
    for table in CHANPARM.TBL GENPARM.TBL HYDRO.TBL MPTABLE.TBL SOILPARM.TBL; do
        ln -s "$build_dir/Run/$table" "$run_dir/$table"
    done
}

run_model() {
    local run_dir=$1
    (cd "$run_dir" && mpiexec -n "$SLURM_NTASKS" ./wrf_hydro.exe > model.log 2>&1)
    grep -q "The model finished successfully" "$run_dir/model.log"
}

spinup="$test_root/spinup_6h"
continued="$test_root/continued_42h"
continuous="$test_root/continuous_48h"
prepare_run "$spinup"
prepare_run "$continued"
prepare_run "$continuous"

# Produce a paired land/hydro restart at 06 UTC without daily-resolution output.
sed -i 's/^KDAY = 7/! KDAY = 7/; s/^! KHOUR = 8/KHOUR = 6/; s/RESTART_FREQUENCY_HOURS = 24/RESTART_FREQUENCY_HOURS = 6/' \
    "$spinup/namelist.hrldas"
sed -i 's/rst_dt = 1440/rst_dt = 360/' "$spinup/hydro.namelist"
ln -s "$example_dir/NWM/RESTART" "$spinup/RESTART"
run_model "$spinup"

land_restart="$spinup/RESTART.2011082606_DOMAIN1"
hydro_restart="$spinup/HYDRO_RST.2011-08-26_06:00_DOMAIN1"
test -f "$land_restart"
test -f "$hydro_restart"

# Restart at 06 UTC. The first 18 hours are intentionally incomplete; only 27 August may publish.
sed -i 's/START_HOUR  = 00/START_HOUR  = 06/; s/^KDAY = 7/! KDAY = 7/; s/^! KHOUR = 8/KHOUR = 42/; s/RESTART_FREQUENCY_HOURS = 24/RESTART_FREQUENCY_HOURS = 42/' \
    "$continued/namelist.hrldas"
sed -i "s|RESTART_FILENAME_REQUESTED = .*|RESTART_FILENAME_REQUESTED = '$land_restart'|" "$continued/namelist.hrldas"
sed -i "s|RESTART_FILE  = .*|RESTART_FILE = '$hydro_restart'|; s/rst_dt = 1440/rst_dt = 2520/; s/RSTRT_SWC = 1/RSTRT_SWC = 0/" "$continued/hydro.namelist"
sed -i '/^[[:space:]]*t0OutputFlag[[:space:]]*=/a\CHRTOUT_HOURLY = 0\nCHRTOUT_DAILY = 1\nLDASOUT_HOURLY = 0\nLDASOUT_DAILY = 1' "$continued/hydro.namelist"
run_model "$continued"

# A continuous 48-hour control reaches the identical terminal time and writes a comparison restart.
sed -i 's/^KDAY = 7/! KDAY = 7/; s/^! KHOUR = 8/KHOUR = 48/; s/RESTART_FREQUENCY_HOURS = 24/RESTART_FREQUENCY_HOURS = 48/' \
    "$continuous/namelist.hrldas"
sed -i 's/rst_dt = 1440/rst_dt = 2880/' "$continuous/hydro.namelist"
ln -s "$example_dir/NWM/RESTART" "$continuous/RESTART"
run_model "$continuous"

"/home/mpan/local/miniforge3/bin/conda" run --no-capture-output --name hydro-ops \
    python - "$continued" "$continuous" <<'PY'
import sys
from pathlib import Path

import numpy as np
import xarray as xr

continued = Path(sys.argv[1])
continuous = Path(sys.argv[2])
for product in ("CHRTOUT", "LDASOUT"):
    files = sorted(continued.glob(f"*.{product}_DOMAIN1.daily"))
    assert [path.name[:8] for path in files] == ["20110827"], files
assert not list(continued.glob("20110826.*.daily")), "incomplete initial day was published"

def compare_restart(left_path: Path, right_path: Path) -> int:
    compared = 0
    with xr.open_dataset(left_path, decode_times=False) as left, xr.open_dataset(
        right_path, decode_times=False
    ) as right:
        for name in sorted(set(left.data_vars) & set(right.data_vars)):
            if "ACC" in name.upper() or name.lower() in {"times", "reference_time"}:
                continue
            if left[name].dtype.kind not in "iuf" or left[name].shape != right[name].shape:
                continue
            np.testing.assert_allclose(
                left[name].values,
                right[name].values,
                rtol=1.0e-6,
                atol=1.0e-7,
                equal_nan=True,
                err_msg=f"restart variable {name}",
            )
            compared += 1
    return compared

land_count = compare_restart(
    continued / "RESTART.2011082800_DOMAIN1",
    continuous / "RESTART.2011082800_DOMAIN1",
)
hydro_count = compare_restart(
    continued / "HYDRO_RST.2011-08-28_00:00_DOMAIN1",
    continuous / "HYDRO_RST.2011-08-28_00:00_DOMAIN1",
)
assert land_count and hydro_count
print("incomplete_initial_day_suppressed=true")
print("complete_post_restart_days=1")
print(f"continuous_restart_variables_compared={land_count + hydro_count}")
PY

echo "test_root=$test_root"
