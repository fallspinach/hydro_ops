# Hydro Ops

Portable workflows for meteorological forcing, hydrologic model runs, and analysis on SLURM.

The verified cluster build procedure for the WRF-Hydro 5.4.0 code corresponding to operational
NWM 3.1 is documented in [WRF-Hydro 5.4.0 build on AWARE](docs/wrf_hydro_build.md).
The authoritative public NWM 3.1.6 CONUS static-input inventory, downloader, compatibility
boundaries, and initialization strategy are documented in
[NWM 3.1 operational inputs](docs/nwm_operational_inputs.md).

## Python environment

The installed environment is not stored in Git. Its portable specification is
tracked in `environment.yml` and uses only conda-forge packages. With Miniforge:
The specification also installs this repository as an editable package, providing the `hydro-ops` command.

```bash
mamba env create --file environment.yml
conda activate hydro-ops
```

After editing the specification, synchronize an existing environment with:

```bash
mamba env update --name hydro-ops --file environment.yml --prune
```

For non-interactive SLURM jobs, use the environment without shell activation:

```bash
/home/mpan/local/miniforge3/bin/conda run --no-capture-output --name hydro-ops COMMAND
```

## Layout

```text
src/       installable Python package and command-line interface
config/    portable TOML defaults and ignored local overrides
slurm/     batch entry points
tests/     unit tests
data/      forcing/<provider>/<product>, observations, static, model_inputs
outputs/   model_runs and analysis products
work/      temporary/intermediate files
logs/      job logs
```

Downloaded data, outputs, work files, logs, credentials, and `config/local.toml` are ignored by Git. Empty directory markers only are tracked.

## Recurring forcing refresh

`bin/update_forcing.py` reports the latest valid time and file count for every maintained
forcing stream, then submits NLDAS-2, Stage-IV, PRISM, HRRR, and MRMS refresh jobs. It
checks SLURM first and skips a workflow when a job with that name is already pending or
running. A non-blocking lock also prevents simultaneous updater processes.

```bash
# Report only.
python bin/update_forcing.py --status-only

# Report and submit missing refresh jobs.
python bin/update_forcing.py

# Preview submissions.
python bin/update_forcing.py --dry-run
```

The canonical project schedule is tracked in `cron/hydro_ops.crontab`, including download
refresh and rolling forcing-production submission. Copy its reviewed entries into `crontab -e`.
The forcing refresh entry runs every six hours using the project environment:

```cron
0 */6 * * * cd /cw3e/mead/projects/cwp206/agentization/hydro_ops && /home/mpan/local/miniforge3/bin/conda run --no-capture-output --name hydro-ops python bin/update_forcing.py >> logs/update-forcing.log 2>&1
```

The individual workflows retain their configured latency and refresh behavior: NLDAS-2
targets its lagged day, Stage-IV refreshes realtime plus archive data, PRISM checks its
revision window, HRRR retrieves the previous complete UTC day, and MRMS refreshes recent
Pass 1, Pass 2, and quality fields.

## NWM 1-km target grid

The remapping environment includes CDO, NCO, and ESMF. A sample daily NWM LDASIN file
can be reduced to reusable CF and SCRIP grid descriptions without loading the full file
into memory:

```bash
python bin/extract_nwm_grid.py \
  data/static/nwm/forcing_grid/20250101.LDASIN_DOMAIN1 \
  --target data/static/nwm/forcing_grid/nwm_conus_1km_grid.nc \
  --scrip data/static/nwm/forcing_grid/nwm_conus_1km_scrip.nc
```

The CF target file contains the 2-D cell centers, four cell corners, grid indices, and
the stable active-domain mask extracted from the sample. The SCRIP file contains the
same centers, corners, dimensions, and mask flattened in row-major order for CDO/ESMF
weight generation. Use `--force` to replace existing outputs deliberately.

### Elevation data

Elevation adjustments use the public-domain USGS GMTED2010 mean-elevation product at
30 arc-seconds (approximately 1 km). It uses WGS84 horizontal coordinates and EGM96
orthometric heights in metres. Download the official global archive and derive a
compressed GeoTIFF covering the entire NWM geographic envelope with:

```bash
python bin/download_gmted2010.py
```

The archive is retained below `data/static/dem/gmted2010/mean_30arcsec/raw`, and the
derived file is
`data/static/dem/gmted2010/mean_30arcsec/gmted2010_mean_30arcsec_nwm_extent.tif`.
Reruns validate and reuse the archive and extracted grid; pass `--force` to deliberately
rebuild the derived GeoTIFF.

Sample the DEM at active NWM forcing-cell centers to create the target elevation used by the
downscaling physics:

```bash
python bin/create_nwm_elevation.py \
  data/static/dem/gmted2010/mean_30arcsec/gmted2010_mean_30arcsec_nwm_extent.tif \
  data/static/nwm/forcing_grid/nwm_conus_1km_grid.nc \
  data/static/nwm/forcing_grid/nwm_conus_1km_elevation.nc
```

The sampler operates in row chunks, applies the NWM active-domain mask, and records the DEM,
target grid, and interpolation method in the output. Download the authoritative NASA NLDAS
mean elevation as the source terrain for NLDAS temperature, pressure, humidity, and longwave
adjustment rather than inferring a new terrain surface:

```bash
mkdir -p data/static/nldas2
curl --fail --location \
  --output data/static/nldas2/NLDAS_elevation.nc4 \
  https://ldas.gsfc.nasa.gov/sites/default/files/ldas/nldas/NLDAS_elevation.nc4
```

HRRR terrain and native grid metadata are acquired once per accepted grid definition without
adding a ninth field to every hourly forcing subset:

```bash
python bin/download_hrrr_static.py --cycle 2022100100
```

This writes the native `HGT:surface:anl` record, its NetCDF conversion, and the complete
`wgrib2 -grid` description below `data/static/hrrr/conus`. The grid description also preserves
the GRIB wind-orientation flag; the current CONUS grid reports grid-relative winds, which must
be rotated to earth-relative `U2D` and `V2D` before publication.

Individual forcing files can be checked for required fields, exact units, valid time, and a
reproducible native-grid fingerprint while the archive download is still running:

```bash
python bin/inventory_forcing_file.py --product nldas2 path/to/NLDAS_FILE.nc
python bin/inventory_forcing_file.py --product hrrr path/to/hrrr_forcing.grib2.nc
python bin/inventory_forcing_file.py --product prism_tmin path/to/prism_tmin.nc
```

The command prints one JSON record per input and returns a nonzero status if any file fails
structural validation.

Report archive completeness over an inclusive range without opening every completed file:

```bash
python bin/report_forcing_completeness.py \
  --start 2022-10-01 --end 2025-09-30 \
  --product nldas2 --product hrrr \
  --product prism_tmin --product prism_tmax --product prism_tmean
```

The report lists missing valid times and returns nonzero while any requested day is incomplete;
add `--json` for machine-readable retry/orchestration input.

Generate reusable direct source-to-NWM weights from one structurally validated example file:

```bash
python bin/generate_forcing_weights.py \
  --source path/to/NLDAS_FORA0125_H.nc \
  --product nldas2 --variable Tair \
  --target-grid data/static/nwm/forcing_grid/nwm_conus_1km_grid.nc \
  --output data/static/remapping/nwm_conus_1km/nldas2_bilinear.nc \
  --method bilinear
```

Each weight file has a JSON manifest containing source and target coordinate fingerprints,
the selected method and variable, the exact command, and the CDO version. Use bilinear weights
for continuous state/flux fields and conservative weights for precipitation depth. HRRR's
wgrib2 NetCDF conversion does not retain cell corners, so its conservative weight generation
uses the paired native GRIB as the geometry source while the NetCDF remains the validated grid
identity:

```bash
python bin/generate_forcing_weights.py \
  --source path/to/hrrr_forcing.grib2.nc --product hrrr --variable APCP_surface \
  --cdo-source path/to/hrrr_forcing.grib2 --cdo-variable tp \
  --target-grid data/static/nwm/forcing_grid/nwm_conus_1km_grid.nc \
  --output data/static/remapping/nwm_conus_1km/hrrr_conservative.nc \
  --method conservative
```

Process one NLDAS-2 thermodynamic hour as a coupled temperature, pressure, humidity, and
downward-longwave bundle:

```bash
python bin/process_thermodynamic_hour.py \
  --source path/to/NLDAS_FORA0125_H.nc \
  --product nldas2 \
  --source-elevation data/static/nldas2/NLDAS_elevation.nc4 \
  --source-elevation-variable NLDAS_elev \
  --target-grid data/static/nwm/forcing_grid/nwm_conus_1km_grid.nc \
  --target-elevation data/static/nwm/forcing_grid/nwm_conus_1km_elevation.nc \
  --weights data/static/remapping/nwm_conus_1km/nldas2_bilinear.nc \
  --output outputs/forcing/thermodynamic/2022080100.nc
```

Use `--product hrrr` with the HRRR bilinear weights and static `HGT_surface` terrain. The
processor diagnoses RH, normalizes all four coupled fields to sea level, applies one common
remap operation, restores NWM elevation, then reconstructs humidity and longwave consistently.
An optional `--final-temperature` supplies the later PRISM-constrained target temperature.
Temporary remap files default to the output filesystem, avoiding capacity-limited system
`/tmp`; `--work-directory` selects another scratch location. The operational default tolerates,
clips, and flags source RH up to 110%; larger excursions reject the hour. Override this audited
threshold with `--relative-humidity-tolerance` when an evaluation supports a different value.

Process shortwave and the paired wind components for the same selected source hour with:

```bash
python bin/process_radiation_wind_hour.py \
  --source path/to/hrrr_forcing.grib2.nc \
  --product hrrr \
  --target-grid data/static/nwm/forcing_grid/nwm_conus_1km_grid.nc \
  --weights data/static/remapping/nwm_conus_1km/hrrr_bilinear.nc \
  --output outputs/forcing/radiation_wind/2022080100.nc
```

For HRRR, `UGRD` and `VGRD` are rotated from its native Lambert grid to earth-relative eastward
and northward components before bilinear interpolation. NLDAS-2 components are already
earth-relative. `SWDOWN` is remapped directly, small negative numerical values are clipped and
flagged, and positive flux is forced to zero where target solar geometry is below the default
−0.833° twilight horizon. The output includes `U2D`, `V2D`, `SWDOWN`, solar-zenith diagnostics,
provenance, and a CF-style QC bit mask.

Produce a seven-field hourly LDASIN file with automatic whole-hour source selection:

```bash
python bin/produce_forcing_hour.py 2022121212 \
  --nldas2-root data/forcing/nasa/nldas2/fora0125_hourly_v2.0 \
  --hrrr-root data/forcing/noaa/hrrr/conus/3km/hourly \
  --target-grid data/static/nwm/forcing_grid/nwm_conus_1km_grid.nc \
  --target-elevation data/static/nwm/forcing_grid/nwm_conus_1km_elevation.nc \
  --nldas2-elevation data/static/nldas2/NLDAS_elevation.nc4 \
  --hrrr-elevation data/static/hrrr/conus/hrrr_static.2022100100.grib2.nc \
  --nldas2-weights data/static/remapping/nwm_conus_1km/nldas2_bilinear.nc \
  --hrrr-weights data/static/remapping/nwm_conus_1km/hrrr_bilinear.nc \
  --output outputs/forcing/2022121212.LDASIN_DOMAIN1
```

The selector prefers a structurally valid, exact-time NLDAS-2 file and falls back to HRRR as a
whole bundle. Every run verifies the source and target grid fingerprints against the selected
weight manifest and validates/fingerprints both terrain files. The output matches the sample's
seven completed variable names, dimensions, coordinates, bounds, units, and `-9.99e8` fill
value. It deliberately omits `RAINRATE` and marks precipitation as pending, so this intermediate
file cannot be mistaken for a complete model forcing file.

Prepare the reusable PRISM-grid elevation once, then create a daily target-grid Tmin/Tmax
constraint:

```bash
python bin/create_prism_elevation.py \
  data/static/dem/gmted2010/mean_30arcsec/gmted2010_mean_30arcsec_nwm_extent.tif \
  path/to/prism_tmin_example.nc \
  data/static/prism/prism_an_4km_elevation.nc

python bin/prepare_prism_temperature.py \
  --minimum path/to/prism_tmin_day.nc --maximum path/to/prism_tmax_day.nc \
  --source-elevation data/static/prism/prism_an_4km_elevation.nc \
  --target-grid data/static/nwm/forcing_grid/nwm_conus_1km_grid.nc \
  --target-elevation data/static/nwm/forcing_grid/nwm_conus_1km_elevation.nc \
  --weights data/static/remapping/nwm_conus_1km/prism_bilinear.nc \
  --output work/prism_temperature_day.nc
```

`apply_prism_temperature_day.py` consumes exactly 24 contiguous preliminary thermodynamic
files for the PRISM 12Z-to-12Z window. It writes a 24-hour corrected `T2D` file plus midpoint,
range-scale, and safeguard diagnostics. Pass that file as `--final-temperature` when producing
each hour; the processor selects the matching timestamp and consistently reconstructs `Q2D`
and `LWDOWN` around the corrected temperature.

For revision processing, `produce_prism_constrained_day.py --complete-root PATH` reuses existing
eight-field LDASIN hours. It changes only `T2D`, `Q2D`, and `LWDOWN`, preserving precipitation
and avoiding source remapping. Omit `--complete-root` only when precipitation must be produced
for the first time.

### Complete forcing production

The precipitation processor conservatively remaps every available exact-time candidate and
selects a single source at each NWM cell. Its initial deterministic policy is: configured
Stage-IV override, acceptable MRMS Pass 2, acceptable Pass 1, Stage-IV archive/realtime,
NLDAS-2, then HRRR. MRMS negative no-coverage codes remain missing. The initial quality-index
threshold is 0.5 and is explicitly a calibration candidate, not a frozen conclusion.

```bash
python bin/process_precipitation_hour.py \
  --candidate mrms_pass2=path/to/pass2.nc \
  --candidate mrms_pass1=path/to/pass1.nc \
  --candidate stage4_archive=path/to/stage4.nc \
  --candidate nldas2=path/to/nldas.nc \
  --candidate hrrr=path/to/hrrr.nc \
  --weights mrms_pass2=data/static/remapping/nwm_conus_1km/mrms_conservative.nc \
  --weights mrms_pass1=data/static/remapping/nwm_conus_1km/mrms_conservative.nc \
  --weights stage4_archive=data/static/remapping/nwm_conus_1km/stage4_conservative.nc \
  --weights nldas2=data/static/remapping/nwm_conus_1km/nldas2_conservative.nc \
  --weights hrrr=data/static/remapping/nwm_conus_1km/hrrr_conservative.nc \
  --quality path/to/mrms_quality.nc \
  --quality-weights data/static/remapping/nwm_conus_1km/mrms_quality_bilinear.nc \
  --target-grid data/static/nwm/forcing_grid/nwm_conus_1km_grid.nc \
  --remap-grid data/static/nwm/forcing_grid/nwm_conus_1km_scrip.nc \
  --output work/precipitation.nc
```

For a complete 12Z-to-12Z forcing day with consistent source availability, batch all 24 hours
so each multi-gigabyte remapping operator is loaded only once:

```bash
python bin/process_precipitation_day.py \
  --day 2026-01-20 \
  --output-directory work/precipitation/20260120
```

The daily processor preserves the hourly output schema and source/QC provenance. It rejects a
day whose candidate set changes within the window so the caller can use the hourly fallback.

Run complete, resumable eight-variable production over an inclusive UTC-hour range with:

```bash
python bin/produce_forcing_range.py --start 2026072410 --end 2026072412
```

Each hour discovers available revisions, runs the seven-field path, composites precipitation,
and atomically publishes a complete LDASIN plus JSON manifest below `outputs/forcing/nwm`.
Existing structurally complete hours are skipped. `--continue-on-error` reports unavailable
hours without stopping an entire range.

For batch operation, `slurm/produce_forcing.py` processes one hour per SLURM array task. The
reviewable schedule in `cron/hydro_ops.crontab` submits a resumable 72-hour rolling window every
six hours, offset from download submission and capped at four concurrent tasks. Test a
submission without changing external state using:

```bash
python bin/submit_forcing_production.py --force --dry-run
```

For retrospective production, use daily batching instead. It loads each static remapping
operator once for 24 source timesteps while still publishing 24 independent hourly LDASIN
files. One UTC day is assigned to each resumable array task; the default concurrency is 16 and
each task stages intermediates on node-local scratch:

```bash
python bin/submit_forcing_days.py --start 2025-10-01 --end 2026-08-24 --dry-run
```

The hourly array remains the fallback for partial days or days on which source availability
changes within the day.

Within each daily task, precipitation operators are applied sequentially because concurrent
CDO remaps were slightly slower on node-local scratch in benchmarking. The 24 final hours are
assembled with four process workers. The SLURM entry point reserves 12 CPUs (approximately
24 GB under the cluster allocation policy) to accommodate their measured aggregate memory.
Override these cautiously with `--precipitation-remap-workers` and `--assembly-workers`;
increasing workers also increases node-local and permanent-storage I/O.

Calibration and validation metrics are deliberately separate from production settings:

```bash
python bin/evaluate_forcing.py \
  --reference observations.nc --reference-variable precipitation \
  --candidate forcing.nc --candidate-variable RAINRATE \
  --strata terrain_classes.nc --strata-variable elevation_class
```

The framework reports finite-pair bias, MAE, RMSE, correlation, arbitrary stratifications, and
MRMS quality-threshold sweeps. It provides reproducible evaluation machinery while the archive
is growing; it does not claim that any threshold has been calibrated or independently validated.

PRISM daily precipitation reconciliation consumes exactly 24 chronological LDASIN hours for
the `(D-1 12Z, D 12Z]` day and conservative **NWM-to-PRISM** weights. It writes corrected hours
to a separate directory and a daily diagnostic file; inputs are never edited in place. Use a
stable PRISM release for retrospective final output, and label mutable runs `early` or
`provisional`:

```bash
python bin/reconcile_prism_precipitation_day.py path/to/24/hourly/files/* \
  --prism path/to/prism_ppt_day.nc \
  --weights data/static/remapping/nwm_conus_1km/nwm_to_prism_conservative.nc \
  --revision stable --output-directory outputs/forcing/nwm_prism_final/day \
  --diagnostics outputs/forcing/diagnostics/prism_day.nc
```

The conservative operator is intentionally the reverse of the existing PRISM-to-NWM weight
file; do not reuse `prism_conservative.nc`. Generate and fingerprint it with:

```bash
python bin/generate_prism_reconciliation_weights.py \
  --nwm-grid data/static/nwm/forcing_grid/nwm_conus_1km_grid.nc \
  --prism-grid path/to/prism_ppt_day.nc \
  --output data/static/remapping/nwm_conus_1km/nwm_to_prism_conservative.nc
```

The solver preserves native hourly
fractions, applies bounded multiplicative projections until reaggregation meets tolerance,
and records missing constraints, dry/wet synthesis, ratio caps, residuals, and convergence.

Run the complete temperature and precipitation constraint and publish only the final 24-record
daily LDASIN collection with:

```bash
python bin/produce_prism_constrained_daily.py \
  --day 2026-07-15 \
  --complete-root outputs/forcing/nwm \
  --output-root outputs/forcing/nwm_prism \
  --revision provisional
```

The input root may contain either individual hourly LDASIN files or daily LDASIN collections.
When only daily collections remain, exact hourly records are extracted to node-local job scratch
and removed after publication; persistent hourly copies are therefore not required. The command
atomically publishes the constrained daily collection, its manifest, and precipitation
diagnostics.

Create a verified calendar-day baseline collection before removing its hourly inputs with:

```bash
python bin/archive_nwm_forcing_day.py \
  --day 2026-07-15 \
  --hourly-root outputs/forcing/nwm \
  --output-root outputs/forcing/nwm \
  --delete-hourly
```

Daily-batched production submitted with `bin/submit_forcing_days.py` performs this aggregation and
verified hourly cleanup by default. Use `--keep-hourly` only for debugging. The canonical daily
name is `YYYY/MM/YYYYMMDD.LDASIN_DOMAIN1` without a `.nc` suffix, matching NWM forcing naming;
readers retain compatibility with older `.nc` daily collections.

The revision-aware rolling scheduler revisits the configured PRISM refresh window, requires a
complete baseline and all three daily PRISM inputs, and submits only missing, stale, or
revision-transitioned days as a bounded SLURM array:

```bash
python bin/submit_prism_forcing_updates.py \
  --stream nrt \
  --complete-root outputs/forcing/nwm \
  --output-root outputs/forcing/nwm_prism/nrt \
  --dry-run
```

Current-month days are labeled `early`, older mutable days `provisional`, and days at least 183
days old `stable`. The `nrt` stream retains early/provisional forcing below
`outputs/forcing/nwm_prism/nrt`; the `retro` stream independently publishes stable forcing below
`outputs/forcing/nwm_prism/retro`. Stable publication therefore never replaces the retained NRT
record. Source modification times and the revision stored in each output are used to decide
whether it must be rebuilt. The canonical recurring entries are in `cron/hydro_ops.crontab`.

The operational cadence has three passes:

| Pass | Schedule (local time) | Scan window |
|---|---:|---:|
| Fast NRT | 02:45, 08:45, 14:45, 20:45 daily | Latest 10 eligible days |
| NRT reconciliation | 04:30 daily | Latest 200 eligible days |
| Stable retrospective | 05:30 on the 18th monthly | 45 days ending at the six-month boundary |

The retrospective window is deliberately offset: it scans newly stable dates approximately six
months behind the current date, not the latest 45 calendar days. Existing current outputs are
skipped, so the windows provide recovery coverage without rebuilding every file on every scan.

Regional Stage-IV calibration uses an NPZ sample table containing equally shaped arrays named
`reference`, `quality`, `strata`, `group`, `mrms_pass2`, and `stage4_archive`. `group` must label
a whole storm or 12Z-to-12Z day so correlated hours cannot cross the split. The command hashes
whole groups deterministically, fits rules only on the calibration subset, and then reports the
selected rules once on the withheld subset:

```bash
python bin/calibrate_precipitation_overrides.py \
  --samples work/evaluation/precipitation_samples.npz \
  --output outputs/evaluation/stage4_override_calibration.json
```

The resulting JSON remains an evaluation artifact. Promote its rules into production only
after checking regional sample counts, gauge independence, basin-total skill, event detection,
and stability across seasons and water years.

The complete evaluation protocol and promotion checklist are documented in
`docs/precipitation_calibration_validation.md`.

The pre-production scenario matrix and promotion gates for the independently retained NRT and
stable retrospective streams are documented in `docs/forcing_stream_validation_plan.md`.

The complete eight-variable production design is documented in
`docs/forcing_production_workflow.md`. It defines common source-selection and revision rules,
direct remapping, cross-variable coupling, elevation adjustments, quality control, provenance,
publication, calibration, and implementation phases. The more detailed quality-aware
precipitation compositing and PRISM daily reconciliation algorithm is documented in
`docs/precipitation_production_workflow.md`.

The corresponding air-temperature workflow is documented in
`docs/temperature_production_workflow.md`. It defines NLDAS-2 as the preferred retrospective
hourly baseline, HRRR analysis as the provisional latency-gap source, PRISM Tmin/Tmax as daily
constraints, and a fixed-lapse-rate elevation normalization before direct remapping to the NWM
grid. It also specifies additional data coverage and static terrain inputs needed before
implementation and evaluation.

## NLDAS-2 setup

The requested archive is NASA GES DISC's NLDAS-2 primary hourly forcing (File A), not a NOAA-hosted archive. It requires a free NASA Earthdata Login account and authorization for the **NASA GESDISC DATA ARCHIVE** application.

Create `~/.netrc` (never put this file in the project):

```text
machine urs.earthdata.nasa.gov login YOUR_EARTHDATA_USERNAME password YOUR_EARTHDATA_PASSWORD
```

Then protect it with `chmod 600 ~/.netrc`. If credentials live elsewhere, set `netrc` under `[nldas2]` in the ignored `config/local.toml`, or export `HYDRO_OPS_NLDAS_EARTHDATA_NETRC`.
The downloader maintains Earthdata's session cookies in `~/.urs_cookies` with mode 600.

Test discovery without network/authentication:

```bash
hydro-ops download nldas2 --date 2026-08-01 --dry-run
```

Download one day interactively or submit it through SLURM:

```bash
hydro-ops download nldas2 --date 2026-08-01
hydro-ops submit nldas2 --date 2026-08-01
```

A date range is inclusive:

```bash
hydro-ops submit nldas2 --start 2026-08-01 --end 2026-08-07
```

With no date argument, the job fetches five UTC days ago because NLDAS near-real-time data typically lag several days. Change `lag_days` in `config/local.toml`. Reruns compare valid local files with server size and modification time, incomplete downloads use `.part`, and up to four files download concurrently by default.

To run daily, add this line with `crontab -e` on a host where cron is supported (adjust the absolute path after migration):

```cron
15 8 * * * cd /cw3e/mead/projects/cwp206/agentization/hydro_ops && /home/mpan/local/miniforge3/bin/conda run --name hydro-ops hydro-ops submit nldas2
```

The submission requests one node, one task, and `download_jobs` CPUs on `shared-128`. Override paths, partition, account, time, lag, or concurrency in `config/local.toml`; see `config/project.toml` for all variables.

## Stage-IV setup

Stage-IV precipitation is available without credentials from two NOAA streams. The
realtime NOMADS stream contains individual GRIB2 files and is refreshed on every run
because files can be revised. The stable archive stream contains daily tar files; valid
local copies are skipped when their size and modification time match the server copy.
Only CONUS products are selected. After each download, every native GRIB2 field is
converted separately with `wgrib2` to preserve that reporting hour's full projected grid
and missing-data mask; products with different accumulation periods are never merged.

```bash
# Refresh the configured seven-day realtime lookback and fetch the archive day.
hydro-ops download stage4

# Select one stream and an inclusive date range.
hydro-ops download stage4 --stream realtime --date 2026-08-20
hydro-ops submit stage4 --stream archive --start 2026-07-01 --end 2026-07-31
```

Data are stored below `data/forcing/noaa/stage4/{realtime,archive}`. With no
date arguments, realtime refreshes today plus the preceding seven days, while archive
fetches the day eight UTC days ago. These values and both server URLs are configurable
under `[stage4]` or with `HYDRO_OPS_STAGE4_*` environment variables.
Converted files are stored below `data/forcing/noaa/stage4/netcdf/{realtime,archive}`.
Existing local files can be converted without contacting NOAA:

```bash
hydro-ops convert stage4 --stream both
```

## HRRR hourly forcing

The HRRR workflow extracts only the native-grid CONUS fields required by hydrologic
models from NOAA's public AWS archive. For every valid UTC hour it retrieves 2-m air
temperature and specific humidity, surface pressure, surface downward shortwave and
longwave radiation, and 10-m U/V winds from the `f00` analysis. Hourly precipitation
comes from the preceding cycle's `f01` APCP record because `f00` APCP has a zero-length
accumulation interval. Index-driven HTTP byte ranges avoid downloading the roughly
150-MB full surface files.

```bash
hydro-ops download hrrr --date 2026-08-15
hydro-ops submit hrrr --start 2026-08-01 --end 2026-08-07
```

Each command processes all 24 hours of every requested UTC date. With no date argument,
the configured `lag_days` selects the previous UTC day. Eight-record subset GRIB2 files
and converted NetCDF files are stored below
`data/forcing/noaa/hrrr/conus/3km/hourly/YYYY/MM/DD`. The source URL, lag, concurrency,
timeouts, retries, destination, and `wgrib2` executable are configurable under `[hrrr]`
or through `HYDRO_OPS_HRRR_*` environment variables.

## PRISM daily forcing

The PRISM workflow downloads CONUS AN daily precipitation and mean, maximum, and minimum
temperature at 4-km resolution directly as NetCDF. Before fetching a grid it queries PRISM's release metadata and compares the
release date and grid count with a JSON record beside the local file. Unchanged grids are
not downloaded. With no date arguments, it checks the previous 215 days through one UTC
day ago, covering PRISM's rolling six-month revision cycle with additional margin.

```bash
hydro-ops download prism --date 2026-08-12
hydro-ops download prism --variable tmean --date 2026-08-12
hydro-ops submit prism
hydro-ops submit prism --start 2026-07-01 --end 2026-08-31
```

NetCDF files are stored under
`data/forcing/oregon_state/prism/an/4km/daily/{ppt,tmean,tmax,tmin}/YYYY/MM`. PRISM ZIP packages and
extracted intermediate files use `/scratch/$SLURM_JOB_USER/job_$SLURM_JOB_ID/prism`
inside SLURM jobs and fall back to the project `work/prism` directory otherwise. Stage-IV
archive conversion uses the same scratch policy. The server request delay, lag, refresh
window, URLs, and destination are configurable under `[prism]`.

## MRMS hourly precipitation

The MRMS workflow downloads the CONUS 1-km `MultiSensor_QPE_01H_Pass1` and
`MultiSensor_QPE_01H_Pass2` accumulations plus `RadarAccumulationQualityIndex_01H` from
NOAA's public AWS archive. Passes remain separate: Pass 1 normally arrives about 20
minutes after the valid hour, while the more gauge-complete Pass 2 arrives about 60
minutes after it. Consumers can select Pass 2 when present and fall back to Pass 1.

```bash
# Refresh yesterday and all eligible hours today; unavailable recent Pass 2 is expected.
hydro-ops download mrms
hydro-ops submit mrms

# Strict archive download for complete UTC dates.
hydro-ops download mrms --date 2026-08-15
hydro-ops submit mrms --start 2026-08-01 --end 2026-08-07
```

Timestamped gzip-compressed GRIB2 sources are retained under
`data/forcing/noaa/mrms/conus/1km/hourly/raw/{pass1,pass2,quality}` and native-grid
NetCDF files under the corresponding `netcdf` tree. With no date arguments, the command
refreshes the configured lookback through the most recent hour expected to have Pass 1;
a later run fills in Pass 2. Source, lookback, products, concurrency, retries, timeouts,
destination, and `wgrib2` are configurable under `[mrms]` or with
`HYDRO_OPS_MRMS_*` environment variables.
