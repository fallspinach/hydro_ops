#!/usr/bin/env bash
set -euo pipefail

# Reproducible WRF-Hydro 5.4.0 standalone NWM-style build for AWARE CPU nodes.
if ! type module >/dev/null 2>&1; then
    echo "The cluster module function is unavailable; run this script from a login shell." >&2
    exit 2
fi

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source_dir=${WRF_HYDRO_SOURCE_DIR:-"$project_root/external/wrf_hydro_nwm_public-v5.4.0"}
build_dir=${WRF_HYDRO_BUILD_DIR:-"$source_dir/build-intel"}
expected_commit=49b0fba3e859ef5288b06ec430621be03193f49e

module purge
module load slurm/AWARE/23.02.7
module load cpu/0.21.2
module load intel/2023.2.4.31
module load intel-mpi/2021.14.2.9
module load netcdf-fortran/4.5.3
module load cmake/3.27.7

if [[ ! -d "$source_dir/.git" ]]; then
    git clone --branch v5.4.0 --depth 1 \
        https://github.com/NCAR/wrf_hydro_nwm_public.git "$source_dir"
fi
actual_commit=$(git -C "$source_dir" rev-parse HEAD)
if [[ "$actual_commit" != "$expected_commit" ]]; then
    echo "Expected WRF-Hydro v5.4.0 commit $expected_commit, found $actual_commit" >&2
    exit 2
fi

# WRF-Hydro's bundled FindNetCDF does not recognize the split Spack roots on AWARE.
# Pin every component so CMake cannot select Miniforge's GNU-built nc-config/nf-config.
cmake --fresh -S "$source_dir" -B "$build_dir" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_COMPILER=mpiicc \
    -DCMAKE_Fortran_COMPILER=mpiifort \
    -DNETCDF_INCLUDES="$NETCDF_C_ROOT/include" \
    -DNETCDF_MODULES="$NETCDF_FORTRAN_ROOT/include" \
    -DNETCDF_LIBRARIES="$NETCDF_FORTRAN_ROOT/lib/libnetcdff.so" \
    -DNETCDF_LIBRARIES_C="$NETCDF_C_ROOT/lib/libnetcdf.so" \
    -DNETCDF_CONFIG_EXE="$NETCDF_C_ROOT/bin/nc-config" \
    -DHYDRO_LSM=NoahMP \
    -DWRF_HYDRO=1 \
    -DSPATIAL_SOIL=1 \
    -DWRFIO_NCD_LARGE_FILE_SUPPORT=1 \
    -DWRF_HYDRO_NUDGING=1 \
    -DNWM_META=1 \
    -DNCEP_WCOSS=0

cmake --build "$build_dir" --parallel "${WRF_HYDRO_BUILD_JOBS:-8}"
ctest --test-dir "$build_dir" --output-on-failure
echo "Executable: $build_dir/Run/wrf_hydro_NoahMP.exe"
