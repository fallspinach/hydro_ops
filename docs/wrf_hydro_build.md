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

## Daily forcing input and output collections

The project build applies `patches/wrf_hydro-5.4.0-daily-io.patch` automatically. For
`FORC_TYP=1`, the patched reader first looks for the original hourly
`YYYYMMDDHH.LDASIN_DOMAIN1` file. If it is absent, it opens
`YYYYMMDD.LDASIN_DOMAIN1`, selects the requested hourly record, and keeps that daily file open
until the date changes. Both sequential and MPP land-reader paths are covered. Existing hourly
forcing directories therefore remain valid without a namelist change.

The 24-hour Croton comparison on 2026-08-29 used one MPI rank and the MPP land path. The hourly
case finished in 8.59 seconds and the daily-input case in 6.75 seconds. All 153 channel, routing,
groundwater, lake, land-surface, and observation output files were byte-for-byte identical.
Run the reproducible cluster test with:

```bash
sbatch --output=logs/wrf_hydro/daily-io-%j.out slurm/test_wrf_hydro_daily_io.sh
```

For output, the initial operational implementation writes ordinary hourly model files to node
scratch and then runs `bin/archive_wrf_hydro_outputs_daily.py`. It concatenates only complete
groups, verifies the record count, publishes through an atomic rename, and optionally removes
hourly inputs after validation. In the Croton prototype, 24 hourly `LDASOUT` files occupied
5.3 MB; the level-2 compressed daily collection occupied 484 KB, a roughly 91% reduction. This
scratch-to-daily publication route reduces
permanent filesystem metadata and storage immediately while retaining the unmodified, verified
model writer. A native multi-record writer can be considered later if CONUS benchmarks show
that scratch-file creation itself remains material.

WRF-Hydro v5.4.0's `SPLIT_OUTPUT_COUNT` is not a complete native-daily solution for the NWM
configuration. A 2023 Mid-Atlantic test showed that the supported newer writer
(`io_form_outputs=2`) still produced one-record `LDASOUT`, `RTOUT`, and `CHRTOUT` files when the
land count was set to 24 and the hydro count to its supported single-file value of 0. An
experimental legacy-writer run, after relaxing the hydro count validator, produced one 24-record
`RTOUT` but continued to write hourly `LDASOUT` and compound-channel `CHRTOUT`. Its 24-record
`RTOUT` was uncompressed and 1.05 GB. Therefore the production workflow must retain scratch
aggregation until all three active NWM writers receive and pass a dedicated multi-record patch;
setting the namelist value alone is insufficient.

Daily-resolution model products are specified independently from their one-day file chunks.
The authoritative storage, model-cycle, forcing-endpoint, restart, and aggregation conventions
are documented in `docs/nwm_time_conventions.md`. Operational cycles start from paired 00 UTC
restarts and set `t0OutputFlag = 0`; enabling time-zero output is diagnostic-only because it
duplicates the preceding cycle's terminal timestamp.
Hourly and daily-resolution LDASOUT and CHRTOUT are independent products and may be enabled
together. A daily file may mix temporal reductions, so `daily` describes the data resolution and
does not imply that every variable is a mean. Each time-varying variable records its own CF
`cell_methods` value. The initial reviewed mapping is
`config/wrf_hydro_daily_reducers.toml`; running accumulations `ACSNOM` and `ACCET` remain omitted
until native interval-difference and reset behavior is implemented. Source tracing confirmed that
`qBtmVertRunoff` is the interval groundwater-inflow volume `qin_gwsubbas` in cubic metres, so its
daily method is `sum`; the other CHRTOUT flow and velocity fields use `mean`.

`bin/reduce_wrf_hydro_daily_output.py` is the offline validation oracle. It requires exactly 24
regularly spaced hourly records, preserves missing samples, writes an explicit aggregation period
through `time_bounds`, and attaches the reducer to each variable. Native Fortran daily output must
match this oracle before hourly LDASOUT is disabled. Reference Mid-Atlantic products are written
under `work/wrf_hydro_daily_oracle` and use names such as `20230121.LDASOUT.daily` and
`20230121.CHRTOUT.daily`.

The native implementation now covers both reach-based CHRTOUT and gridded LDASOUT. Four integer
switches in `HYDRO_nlist` are independent: `CHRTOUT_HOURLY`, `CHRTOUT_DAILY`,
`LDASOUT_HOURLY`, and `LDASOUT_DAILY`. Each accepts 0 or 1. Hourly output defaults to on and daily
output defaults to off, preserving upstream behavior for existing namelists. Daily accumulation excludes
time-zero output, requires hourly output intervals, discards an incomplete initial UTC day, and
publishes at the next complete UTC boundary as `YYYYMMDD.{CHRTOUT,LDASOUT}_DOMAIN1.daily`.
For day `D`, the 24 completed endpoint samples are `D 01` through `D+1 00`, the product is named
for `D`, its representative time is `D 12`, and its bounds are `[D 00,D+1 00]`. This endpoint
sequence is the natural output of a 24-hour integration initialized at `D 00`; it is distinct
from the `00-23` timestamp grouping used for hourly calendar storage.

CHRTOUT accumulates on local reach arrays before the existing MPI gather. Streamflow, nudging,
velocity, surface lateral runoff, and bucket outflow are means; `qBtmVertRunoff` is a sum. LDASOUT
uses means for its selected fluxes and states and `last` for categorical `ISNOW`; it respects the
model's configured output-variable set rather than enabling otherwise disabled fields. Running
accumulations `ACSNOM` and `ACCET` remain absent from daily output. Every included variable records
its own `cell_methods`, the daily time coordinate is the interval midpoint, and `time_bounds`
records the 24-hour UTC period.

Cluster job 4458083 validated both products and the namelist interface in the official 24-hour example. Native daily values
match reductions of all 24 hourly files within their NetCDF packing precision. Daily-only mode
creates no hourly LDASOUT or CHRTOUT files, and both daily files are byte-identical to those from
simultaneous hourly-plus-daily mode. Run the reproducible test with
`slurm/test_wrf_hydro_daily_output.sh`. A daily-resolution-only production configuration is:

```fortran
t0OutputFlag = 0
CHRTOUT_HOURLY = 0
CHRTOUT_DAILY = 1
LDASOUT_HOURLY = 0
LDASOUT_DAILY = 1
```

Set either hourly switch back to 1 when both temporal resolutions are required. Daily products
currently require hourly model output intervals (`out_dt=60` for CHRTOUT and
`OUTPUT_TIMESTEP=3600` for LDASOUT).

After formalizing 00 UTC operational boundaries, job 4464488 reconfirmed the 24 completed
endpoint samples (`01` through next-day `00`) and the `[00,next-00]` bounds. It also demonstrated
that operational `t0OutputFlag=0` products are byte-identical to reductions from a diagnostic
`t0OutputFlag=1` run, proving that the duplicated initial-condition snapshot is excluded.

Cluster job 4458194 extended this to a 54-hour run on two MPI ranks. It produced exactly two
complete daily files for each product, reset both accumulators correctly between days, and did not
publish the incomplete final six-hour period. This test exposed and fixed two multi-day/MPI issues:
non-I/O ranks do not receive the advancing `nlst%olddate`, so CHRTOUT now reconstructs its current
date from the synchronized start time and output counter; LDASOUT resets after variable 98 in the
standard Noah-MP configuration and variable 116 only when Crocus is active. Run this regression
with `slurm/test_wrf_hydro_daily_output_multiday.sh`.

Cluster job 4458220 tested a mid-day restart boundary. A six-hour run wrote paired land and hydro
restarts at 06 UTC; a 42-hour continuation then crossed two UTC boundaries. It correctly omitted
the incomplete 18-hour initial period and published only the complete 27 August LDASOUT and
CHRTOUT daily products. A separate continuous 48-hour control reached the same terminal time, and
89 comparable physical variables across the land and hydro restart files agreed within numerical
precision. Seamless continuation requires `RSTRT_SWC=0`; the upstream example value of 1
deliberately resets restart accumulation fields and produced a scientifically different cycle.
Run this regression with `slurm/test_wrf_hydro_daily_output_restart.sh`.

Example:

```bash
python bin/archive_wrf_hydro_outputs_daily.py "$SLURM_TMPDIR/model_output" \
  --output-dir outputs/nwm/model/retro/2026/01 --day 20260120 --remove-hourly
```

## Domain subsetting

`bin/subset_nwm_domain.py` derives an exact 1-km rectangular grid window from a geographic
bounding box and maps it to the nested 250-m routing grid. Dry-run mode prints a JSON manifest;
`--execute` clips `wrfinput`, `geo_em`, land-output spatial metadata, spatial-soil and hydro
parameters, and the full-domain routing grid with NetCDF-native hyperslabs and compression. It
also rewrites WRF patch dimensions, corners, centers, and GeoTransform origins for the local grid.

The topology stage streams the CONUS spatial-weight table in bounded chunks. It retains runoff
weights whose 250-m cells fall inside the routing window, selects matching channel COMIDs, follows
their downstream paths while those paths remain inside the geographic box, and makes every edge
that leaves the box a terminal outlet. Upstream flow from outside the box is deliberately zero;
this is a structural/development boundary condition, not a scientifically valid replacement for
an upstream inflow hydrograph.

`GWBUCKPARM.ComID` is reordered to exactly match `spatialweights.polyid`, while its local `Basin`
index is rebuilt as contiguous one-based integers. Spatial-weight grid indices are shifted from
global to local one-based indices. RouteLink `from` and `to` references are clipped, its
zero-based `ascendingIndex` is recomputed, and nudging parameters are retained only for gages in
the selected reaches. The utility reopens the written products and verifies all these invariants
before recording `network_files_status: validated` in `subset_manifest.json`.

Every retained reach and catchment also receives two complementary byte masks. A
`boundary_affected` seed is created where a retained reach receives flow from a CONUS reach that
was cut from the subset, or where only part of a catchment's spatial-weight footprint was retained.
That flag is propagated through every retained downstream reach. `complete_watershed=1` therefore
means that the reach's entire modeled upstream drainage network and runoff-collection footprint are
inside the subset; use only these reaches for flow evaluation unless explicit boundary inflows are
provided. The masks are stored on `RouteLink.nc` and `spatialweights.nc`.

`subset_flow_validity.nc` provides matching 250-m spatial masks. Its
`boundary_affected` cells contribute to at least one affected catchment, while
`complete_watershed` cells contribute exclusively to complete catchments. Cells belonging to no
retained catchment have both values set to zero. These spatial masks are diagnostics and are not
required by WRF-Hydro itself.

Lakes, reservoir DA, and diversions remain explicitly disabled because a compatible public
CONUS `LAKEPARM` is unavailable. The subset uses the project's no-lake configuration.

For example, a Croton-area planning run gives a 104 by 91 cell 1-km window and a 416 by 364 cell
routing window:

```bash
python bin/subset_nwm_domain.py \
  --domain-dir data/static/nwm/operational/nwm.v3.1.6/domain \
  --output-dir work/nwm_subset_croton \
  --bbox -74.2 40.9 -73.4 41.7 --execute
```

The 2026-08-30 Croton-area extraction completed in 35 seconds and produced:

- a 104 by 91 cell 1-km land grid and 416 by 364 cell 250-m routing grid;
- 4,210 connected RouteLink reaches with no unresolved downstream IDs;
- 4,096 identically ordered runoff and groundwater catchments;
- 134,049 localized spatial-weight records; and
- 38 matching nudging stations.

Of the 4,210 reaches, 3,578 have complete upstream watersheds and 632 are boundary-affected. The
routing-grid diagnostic identifies 64,650 complete-watershed cells and 20,090 boundary-affected
cells; the remaining routing cells do not contribute to a retained catchment.

A one-hour, one-rank cold-start run then ingested a real project forcing field for
2026-01-19 12 UTC and completed successfully in 2.58 seconds. It wrote land, terrain-routing,
channel, and both land/hydro restart products. Repeating the run with the completeness masks
embedded in `RouteLink.nc` and `spatialweights.nc` also completed successfully. This establishes
structural model compatibility;
longer water/energy balance and warm-start tests remain required before using a subset for
scientific experiments.

### Mid-Atlantic regional scale-up

The next structural test used `--bbox -77.5 38.5 -72.5 43.5`, covering a 632 by 541 cell land
grid and a 2,528 by 2,164 cell routing grid, about 36 times the Croton land area. Extraction and
topology validation completed in 89 seconds and retained 84,419 reaches, 82,815 catchments,
4,047,931 spatial-weight records, and 720 nudging stations. The completeness masks classify
73,908 reaches as complete-watershed and 10,511 as boundary-affected.

A real 24-hour CONUS forcing collection for 2026-01-19 was clipped to the same grid and read
through the daily-input path. A one-hour cold-start run on four MPI ranks completed successfully
in 22.7 seconds wall time, writing land, terrain-routing, channel, and restart products. This
confirms that the subset construction, completeness masks, daily forcing reader, and MPI domain
decomposition operate together at regional scale.

The first 24-hour attempt exposed missing forcing values on static-land cells along the projected
rectangle's coastal outer corners. The unmodified file correctly caused Noah-MP to abort its
shortwave energy check at 13 UTC. This is a forcing/domain-overlap defect, not a routing-topology
failure. A separately labeled diagnostic copy filled only those static-land gaps from the nearest
available grid values; the original forcing collection was preserved. Production runs must use a
preflight coverage check and an explicit, documented edge policy rather than this test-only repair.

With the diagnostic forcing, a 24-hour four-rank run completed successfully in 222.7 seconds.
After initialization, simulated hours averaged about 8.6 seconds. Restarts were written only at
the day boundary, avoiding roughly 7.5 GB of redundant hourly restart traffic. A one-hour warm
continuation from the resulting land and hydro restarts completed in 24.5 seconds. Every physical
variable in its time-zero `LDASOUT` was numerically identical to the continuous run's terminal
state; only the forecast `reference_time` changed, as expected for a new cycle.

### Forcing/domain coverage gate

`bin/preflight_nwm_forcing.py` validates a time-ordered LDASIN window against the subset's static
land mask. A gap is eligible for edge filling only when it is spatially persistent across the
entire requested time window; transient missing values are fatal. Approved gaps are filled from
the nearest valid four-neighbor value with a configurable maximum distance (25 grid cells in the
regional test). The published copies contain a compressed `forcing_edge_fill_mask`, per-variable
repair counts, and the policy metadata. Original forcing files are never modified.

The 24-hour stable-PRISM test window from 2023-01-21 12 UTC through 2023-01-22 11 UTC passed this
gate. It contained 1,364 persistent active-land coverage-mismatch cells, no transient gaps, and a
maximum fill distance of 24 cells. The report is
`work/nwm_subset_mid_atlantic/forcing_prism_20230121_22.coverage.json`. A four-rank, 24-hour
WRF-Hydro run using those constrained and provenance-marked files completed successfully in
223.6 seconds, essentially the same performance as the baseline-forcing run.
