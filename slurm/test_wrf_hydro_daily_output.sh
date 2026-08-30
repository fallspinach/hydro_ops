#!/usr/bin/env bash
#SBATCH --job-name=wrfh-native-daily-output-test
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --time=00:30:00

set -euo pipefail

project_root=${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
source_dir=${WRF_HYDRO_SOURCE_DIR:-"$project_root/external/wrf_hydro_nwm_public-v5.4.0"}
build_dir=${WRF_HYDRO_BUILD_DIR:-"$source_dir/build-intel"}
example_dir="$build_dir/Run/example_case"
scratch_root=${SLURM_TMPDIR:-"/scratch/${SLURM_JOB_USER}/job_${SLURM_JOB_ID}"}
test_root="$scratch_root/wrf_hydro_daily_output"
failure_log="$project_root/logs/wrf_hydro/native-daily-output-${SLURM_JOB_ID:-local}.model.log"
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
sed -i 's/^KDAY = 7/! KDAY = 7/; s/^! KHOUR = 8/KHOUR = 24/' "$test_root/namelist.hrldas"
sed -i '/^[[:space:]]*t0OutputFlag[[:space:]]*=/a\CHRTOUT_HOURLY = 1\nCHRTOUT_DAILY = 1\nLDASOUT_HOURLY = 1\nLDASOUT_DAILY = 1' "$test_root/hydro.namelist"
ln -s "$example_dir/NWM/DOMAIN" "$test_root/DOMAIN"
ln -s "$example_dir/NWM/RESTART" "$test_root/RESTART"
ln -s "$example_dir/NWM/nudgingTimeSliceObs" "$test_root/nudgingTimeSliceObs"
ln -s "$example_dir/FORCING" "$test_root/FORCING"
ln -s "$build_dir/Run/wrf_hydro_NoahMP.exe" "$test_root/wrf_hydro.exe"
for table in CHANPARM.TBL GENPARM.TBL HYDRO.TBL MPTABLE.TBL SOILPARM.TBL; do
    ln -s "$build_dir/Run/$table" "$test_root/$table"
done

(
    cd "$test_root"
    mpiexec -n 1 ./wrf_hydro.exe > model.log 2>&1
)
grep -q "The model finished successfully" "$test_root/model.log"

daily=$(find "$test_root" -maxdepth 1 -name '*.CHRTOUT_DOMAIN1.daily' -print -quit)
test -n "$daily"
daily_ldas=$(find "$test_root" -maxdepth 1 -name '*.LDASOUT_DOMAIN1.daily' -print -quit)
test -n "$daily_ldas"

"/home/mpan/local/miniforge3/bin/conda" run --no-capture-output --name hydro-ops \
    python - "$project_root" "$test_root" "$daily" <<'PY'
import sys
from pathlib import Path

import numpy as np
import xarray as xr

from hydro_ops.wrf_hydro.daily_output import load_reducers, reduce_hourly_files

project = Path(sys.argv[1])
root = Path(sys.argv[2])
daily_path = Path(sys.argv[3])
hourly = sorted(root.glob("*.CHRTOUT_DOMAIN1"))
assert len(hourly) >= 24, len(hourly)
hourly = hourly[-24:]
reducers = load_reducers(project / "config/wrf_hydro_daily_reducers.toml", "CHRTOUT")
expected = reduce_hourly_files(hourly, "CHRTOUT", reducers)
with xr.open_dataset(daily_path) as actual:
    assert actual["time"].attrs["bounds"] == "time_bounds"
    bounds = actual["time_bounds"].values.reshape(-1)
    assert bounds[1] - bounds[0] == np.timedelta64(24, "h")
    for name, method in reducers.items():
        if method == "omit" or name not in actual:
            continue
        assert actual[name].attrs["cell_methods"] == f"time: {method}"
        scale = float(actual[name].encoding.get("scale_factor", 0.01))
        np.testing.assert_allclose(
            actual[name].values,
            expected[name].values,
            rtol=0.0,
            atol=max(scale, 1.0e-6),
            equal_nan=True,
        )
print(f"native_daily={daily_path}")
print(f"validated_hourly_records={len(hourly)}")
PY

"/home/mpan/local/miniforge3/bin/conda" run --no-capture-output --name hydro-ops \
    python - "$project_root" "$test_root" "$daily_ldas" <<'PY'
import sys
from pathlib import Path

import numpy as np
import xarray as xr

from hydro_ops.wrf_hydro.daily_output import load_reducers, reduce_hourly_files

project = Path(sys.argv[1])
root = Path(sys.argv[2])
daily_path = Path(sys.argv[3])
hourly = sorted(root.glob("*.LDASOUT_DOMAIN1"))[-24:]
assert len(hourly) == 24, len(hourly)
reducers = load_reducers(project / "config/wrf_hydro_daily_reducers.toml", "LDASOUT")
with xr.open_dataset(hourly[0]) as sample:
    reducers = {name: method for name, method in reducers.items() if name in sample}
expected = reduce_hourly_files(hourly, "LDASOUT", reducers)
with xr.open_dataset(daily_path) as actual:
    assert actual["time"].attrs["bounds"] == "time_bounds"
    bounds = actual["time_bounds"].values.reshape(-1)
    assert bounds[1] - bounds[0] == np.timedelta64(24, "h")
    for name, method in reducers.items():
        if method == "omit":
            assert name not in actual
            continue
        assert actual[name].attrs["cell_methods"] == f"time: {method}"
        scale = float(actual[name].encoding.get("scale_factor", 0.01))
        np.testing.assert_allclose(
            actual[name].values,
            expected[name].values,
            rtol=0.0,
            atol=max(scale, 1.0e-6),
            equal_nan=True,
        )
print(f"native_daily_ldas={daily_path}")
print(f"validated_ldas_hourly_records={len(hourly)}")
PY

daily_only="$scratch_root/wrf_hydro_daily_output_only"
mkdir -p "$daily_only"
cp "$test_root/namelist.hrldas" "$daily_only/namelist.hrldas"
cp "$test_root/hydro.namelist" "$daily_only/hydro.namelist"
sed -i 's/^CHRTOUT_HOURLY = 1/CHRTOUT_HOURLY = 0/; s/^LDASOUT_HOURLY = 1/LDASOUT_HOURLY = 0/' "$daily_only/hydro.namelist"
for item in DOMAIN RESTART nudgingTimeSliceObs FORCING wrf_hydro.exe \
    CHANPARM.TBL GENPARM.TBL HYDRO.TBL MPTABLE.TBL SOILPARM.TBL; do
    ln -s "$test_root/$item" "$daily_only/$item"
done
(
    cd "$daily_only"
    mpiexec -n 1 ./wrf_hydro.exe > model.log 2>&1
)
grep -q "The model finished successfully" "$daily_only/model.log"
test "$(find "$daily_only" -maxdepth 1 -name '*.CHRTOUT_DOMAIN1' | wc -l)" -eq 0
test "$(find "$daily_only" -maxdepth 1 -name '*.LDASOUT_DOMAIN1' | wc -l)" -eq 0
daily_only_file=$(find "$daily_only" -maxdepth 1 -name '*.CHRTOUT_DOMAIN1.daily' -print -quit)
test -n "$daily_only_file"
cmp "$daily" "$daily_only_file"
daily_only_ldas=$(find "$daily_only" -maxdepth 1 -name '*.LDASOUT_DOMAIN1.daily' -print -quit)
test -n "$daily_only_ldas"
cmp "$daily_ldas" "$daily_only_ldas"
echo "daily_only_byte_identical=true"

echo "test_root=$test_root"
