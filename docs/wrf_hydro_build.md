# WRF-Hydro 5.4.0 build on AWARE

## Source and scope

The public source corresponding to operational NWM 3.1 is WRF-Hydro 5.4.0. The local checkout
is deliberately excluded from Git and is reproducibly obtained from:

- Repository: `https://github.com/NCAR/wrf_hydro_nwm_public.git`
- Tag: `v5.4.0`
- Commit: `49b0fba3e859ef5288b06ec430621be03193f49e`
- Local path: `external/wrf_hydro_nwm_public-v5.4.0`

The verified build is the standalone Noah-MP/WRF-Hydro executable with NWM-style spatial soil,
streamflow nudging, large-file support, and output metadata. It is not by itself a reproduction
of the complete operational NWM 3.1 system; operational domains, parameter files, hydrofabric,
initial states, reservoir configuration, cycling, forcing, and assimilation are separate inputs.

## Upstream requirements

WRF-Hydro 5.4.0 documents these minimums:

| Component | Minimum |
|---|---:|
| Intel Fortran/C compiler | 2018+ |
| MPI | 3.x+ |
| NetCDF-Fortran | 4.4+ |
| CMake | 3.12+ |
| Git | required to obtain pinned source |

MPI is mandatory even for a single-process run. A standalone hydrologic build does not require
WRF, WPS, GRIB2/Jasper, ESMF, or a Python environment. Those tools may be needed for coupled WRF
operation or preprocessing, but not for the executable built here.

## Verified AWARE module stack

```bash
module load slurm/AWARE/23.02.7
module load cpu/0.21.2
module load intel/2023.2.4.31
module load intel-mpi/2021.14.2.9
module load netcdf-fortran/4.5.3
module load cmake/3.27.7
```

The NetCDF-Fortran module automatically loads its compatible dependencies, including
NetCDF-C 4.7.4, HDF5 1.10.6, zlib, libaec, and pkgconf. The tested tools resolve to Intel
Classic `ifort` 2021.10, Intel Classic `icc` 2021.9, Intel MPI 2021.14 with MPI standard 3.1,
NetCDF-Fortran 4.5.3, and CMake 3.27.7.

The compiler wrappers must be `mpiifort` and `mpiicc`. Do not mix GNU or Conda NetCDF libraries
with the Intel objects.

## NetCDF discovery issue

The cluster installs NetCDF-C and NetCDF-Fortran in separate Spack prefixes exposed as
`NETCDF_C_ROOT` and `NETCDF_FORTRAN_ROOT`. WRF-Hydro's bundled `FindNetCDF.cmake` does not infer
that split layout. In addition, this user's Miniforge `bin` directory precedes the module
utilities in `PATH`, so an unqualified `nf-config` resolves to a GNU/Conda installation.

The project build script explicitly supplies the C include/library, Fortran module/library, and
module-provided `nc-config` paths. These arguments are required for a reproducible ABI-consistent
build on this cluster.

## Reproduce the build

From a login shell in the project root:

```bash
bin/build_wrf_hydro.sh
```

The script clones the pinned tag if absent, rejects an unexpected commit, loads the verified
modules, configures a fresh CMake build, compiles with eight jobs, and runs CTest. Override only
the local paths or parallelism when needed:

```bash
WRF_HYDRO_BUILD_JOBS=16 \
WRF_HYDRO_BUILD_DIR=/path/to/build \
bin/build_wrf_hydro.sh
```

The resulting executable and templates are under:

```text
external/wrf_hydro_nwm_public-v5.4.0/build-intel/Run/
  wrf_hydro_NoahMP.exe
  wrf_hydro.exe -> wrf_hydro_NoahMP.exe
  hydro.namelist
  namelist.hrldas
  *.TBL
```

## Verification completed

On 2026-08-22, CMake configuration, the complete compile/link, and both bundled CTest compiler
tests passed. The executable is a 64-bit x86-64 ELF linked to the intended module-provided
`libnetcdff.so.7`, `libnetcdf.so.18`, `libmpifort.so.12`, `libmpi.so.12`, and Intel runtime
libraries. Its runtime search path records the module NetCDF and Intel MPI library prefixes.

The CTest cases only verify compiler behavior. They must be followed by an MPI example run and,
ultimately, a project-domain run with mass-balance and output checks.

## Official Croton MPI smoke test

Configure the upstream seven-day Croton, New York NWM analysis example after building:

```bash
module load slurm/AWARE/23.02.7 cpu/0.21.2 intel/2023.2.4.31 \
  intel-mpi/2021.14.2.9 netcdf-fortran/4.5.3 cmake/3.27.7
cmake --build external/wrf_hydro_nwm_public-v5.4.0/build-intel \
  --target croton-nwm_ana
sbatch --partition=shared-128 --output=logs/wrf_hydro/croton-%j.out \
  slurm/test_wrf_hydro_croton.sh
```

Use Intel MPI's `mpiexec` inside the SLURM allocation. The cluster's `srun --mpi=pmi2` plugin
was tested and produced malformed PMI key/value requests with Intel MPI 2021.14; it must not be
used for this executable. The wrapper requires the success sentinel once per rank and preserves
all outputs below `build-intel/Run/output_nwm_ana_smoke`.

The upstream v5.4.0 setup script also contains a duplicated `nwm_ana` branch and no `nwm` branch,
so the advertised `croton-nwm` target fails setup. `croton-nwm_ana` correctly selects the same
official NWM directory and sets analysis runoff option 7 without modifying third-party source.

On 2026-08-22, job `4422669` ran the complete 168-hour example on two MPI ranks in 63 seconds.
Both ranks emitted the success sentinel. The run created hourly channel, land, lake, groundwater,
and LSM files, three-hourly terrain-routing files, and seven daily land, hydro, and nudging
restart sets. All 1,086 NetCDF output/restart files passed a format-open check, and the final
channel file reports `NWM v3.1`, 168 valid times, and valid time `2011-09-02 00:00:00`.

The model completed successfully, although the original wrapper classified job `4422669` as
failed because it looked for the completion sentinel in `diag_hydro` while v5.4.0 emits it to
standard output. The wrapper now captures model stdout and validates one sentinel per rank.
Clean repeat job `4422685` completed with exit code zero in 75 seconds and confirmed the
corrected wrapper behavior.
