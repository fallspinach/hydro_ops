#!/usr/bin/env bash
#SBATCH --job-name=wrfhydro-daily-io
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --time=00:30:00

set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source_dir=${WRF_HYDRO_SOURCE_DIR:-"$project_root/external/wrf_hydro_nwm_public-v5.4.0"}
build_dir=${WRF_HYDRO_BUILD_DIR:-"$source_dir/build-intel"}
example_dir="$build_dir/Run/example_case"
scratch_root=${SLURM_TMPDIR:-"/scratch/${SLURM_JOB_USER}/job_${SLURM_JOB_ID}"}
test_root="$scratch_root/wrf_hydro_daily_io"

module purge
module load slurm/AWARE/23.02.7 cpu/0.21.2 intel/2023.2.4.31
module load intel-mpi/2021.14.2.9 netcdf-fortran/4.5.3

nco_bin_dir=${NCO_BIN_DIR:-/home/mpan/local/miniforge3/bin}
ncrcat="$nco_bin_dir/ncrcat"
if [[ ! -x "$ncrcat" ]]; then
    echo "ncrcat was not found at $ncrcat; set NCO_BIN_DIR" >&2
    exit 2
fi

mkdir -p "$test_root/hourly" "$test_root/daily" "$test_root/daily_forcing"

for day in 20110826 20110827; do
    "$ncrcat" -O "$example_dir/FORCING/${day}"??.LDASIN_DOMAIN1 \
        "$test_root/daily_forcing/${day}.LDASIN_DOMAIN1"
done

for mode in hourly daily; do
    cp "$example_dir/NWM/namelist.hrldas" "$test_root/$mode/namelist.hrldas"
    cp "$example_dir/NWM/hydro.namelist" "$test_root/$mode/hydro.namelist"
    sed -i 's/^KDAY = 7/! KDAY = 7/; s/^! KHOUR = 8/KHOUR = 24/' \
        "$test_root/$mode/namelist.hrldas"
    ln -s "$example_dir/NWM/DOMAIN" "$test_root/$mode/DOMAIN"
    ln -s "$example_dir/NWM/RESTART" "$test_root/$mode/RESTART"
    ln -s "$example_dir/NWM/nudgingTimeSliceObs" "$test_root/$mode/nudgingTimeSliceObs"
    ln -s "$build_dir/Run/wrf_hydro_NoahMP.exe" "$test_root/$mode/wrf_hydro.exe"
    for table in CHANPARM.TBL GENPARM.TBL HYDRO.TBL MPTABLE.TBL SOILPARM.TBL; do
        ln -s "$build_dir/Run/$table" "$test_root/$mode/$table"
    done
done
ln -s "$example_dir/FORCING" "$test_root/hourly/FORCING"
ln -s "$test_root/daily_forcing" "$test_root/daily/FORCING"

for mode in hourly daily; do
    start=$SECONDS
    (cd "$test_root/$mode" && mpiexec -n 1 ./wrf_hydro.exe > model.log 2>&1)
    grep -q "The model finished successfully" "$test_root/$mode/model.log"
    echo "$mode elapsed_seconds=$((SECONDS - start))"
done

python3 - "$test_root" <<'PY'
import hashlib
import sys
from pathlib import Path

root = Path(sys.argv[1])
suffixes = (".LDASOUT_DOMAIN1", ".CHRTOUT_DOMAIN1", ".RTOUT_DOMAIN1",
            ".GWOUT_DOMAIN1", ".LAKEOUT_DOMAIN1", ".LSMOUT_DOMAIN1",
            ".CHANOBS_DOMAIN1")
hourly = sorted(path for path in (root / "hourly").iterdir() if path.name.endswith(suffixes))
assert hourly, "no comparison outputs"
for path in hourly:
    other = root / "daily" / path.name
    assert other.exists(), f"missing {other.name}"
    assert hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(other.read_bytes()).digest(), path.name
print(f"byte_identical_outputs={len(hourly)}")
PY
