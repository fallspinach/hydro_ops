# NWM 1-km meteorological forcing production workflow

## Operational time and daily-summary convention

NWM hourly forcing is stored in UTC calendar chunks containing timestamps `00-23`. A 24-hour
model cycle initialized from a paired restart at day `D 00` consumes endpoint forcing `D 01`
through `D+1 00`, crossing the chunks for `D` and `D+1`. Model-interval forcing diagnostics use
those same 24 endpoints and bounds `[D 00,D+1 00]`; ordinary timestamp-based `00-23` daily
statistics answer a different question. `bin/reduce_forcing_model_day.py` implements the model-
interval definition, including rate integration, completeness checks, provenance, and explicit
time bounds. See `docs/nwm_time_conventions.md` for the canonical operational timeline.

Full-CONUS job 4464621 reduced the 2026-03-10 NRT model interval in 1 minute 58 seconds with
2.4 GB peak memory. The level-2 compressed one-record product was 216 MB. Its temperature means
and integrated precipitation samples matched direct calculations from hours 01 through next-day
00, and its midpoint and bounds were `2026-03-10 12 UTC` and
`[2026-03-10 00 UTC, 2026-03-11 00 UTC]`.

## Purpose and scope

This document defines the production design for complete hourly meteorological forcing on the
National Water Model (NWM) CONUS 1-km grid. The output contains the eight fields represented by
the sample `LDASIN_DOMAIN1` files:

| NWM variable | Quantity | Units |
|---|---|---|
| `RAINRATE` | precipitation rate | `kg m-2 s-1` |
| `T2D` | 2-m air temperature | `K` |
| `Q2D` | 2-m specific humidity | `kg kg-1` |
| `PSFC` | surface pressure | `Pa` |
| `SWDOWN` | surface downward shortwave radiation | `W m-2` |
| `LWDOWN` | surface downward longwave radiation | `W m-2` |
| `U2D` | 10-m eastward wind | `m s-1` |
| `V2D` | 10-m northward wind | `m s-1` |

The production system combines MRMS, Stage-IV, NLDAS-2, HRRR, and PRISM. Source choice depends
on the variable, latency, revision state, effective resolution, quality information, and
physical consistency. No product is assumed to be universally best merely because its nominal
grid is finer or because it is described as an analysis or reanalysis.

Detailed algorithms for the two variables with external observation-based daily constraints
are maintained separately:

- [Precipitation production workflow](precipitation_production_workflow.md)
- [Air-temperature production workflow](temperature_production_workflow.md)

This master specification is authoritative for cross-variable coupling, common operational
rules, final file assembly, and the production methods for pressure, humidity, radiation, and
wind.

## Design principles

1. **Use observations for constraints, not invented hourly detail.** PRISM can constrain daily
   precipitation and temperature but cannot determine their hourly timing at 1 km.
2. **Preserve physical consistency.** Temperature, pressure, humidity, and longwave radiation
   are processed as a coupled thermodynamic group even when they are stored as separate output
   variables.
3. **Remap directly to the target grid.** An intermediate 4-km forcing grid would discard
   useful MRMS and HRRR structure. Every native grid is mapped directly to the NWM grid.
4. **Separate spatial detail from accuracy.** HRRR provides meaningful mesoscale structure,
   while NLDAS-2 provides a long, consistent, observation-informed retrospective forcing
   record. Resolution alone does not establish lower bias.
5. **Treat recent output as mutable.** Near-real-time hours are revised as MRMS Pass 2,
   Stage-IV, NLDAS-2, and newer PRISM revisions arrive.
6. **Make every decision auditable.** Source, quality, terrain adjustment, daily constraint,
   revision, fallback, and software/weight versions accompany the forcing values.
7. **Calibrate without contaminating evaluation.** Parameters and thresholds are estimated on
   designated calibration years and assessed on withheld years and independent observations.

## Source roles and initial production policy

### Effective source characteristics

NLDAS-2 File A has an hourly 0.125-degree grid, but its non-precipitation meteorology is based
largely on 32-km, 3-hourly NARR analysis fields that were spatially interpolated, temporally
disaggregated, and in several cases adjusted to NLDAS terrain. Its nominal grid spacing must
therefore not be interpreted as 0.125-degree independent atmospheric detail. Its shortwave
radiation has an observation-based GOES/SRB bias correction.

HRRR provides hourly, approximately 3-km analyses tied to HRRR terrain and surface physics. It
offers substantially more mesoscale structure but remains a numerical weather prediction
analysis with model and data-assimilation biases. HRRR analysis is the timely source while
NLDAS-2 is delayed. During overlap, NLDAS-2 supplies the observation-informed large-scale and
hourly retrospective baseline; HRRR is not a wholesale replacement. Instead, an evaluated
hybrid may add only the mesoscale anomaly that HRRR resolves beyond the effective NLDAS-2
scale. This distinction is important because remapping 3-km HRRR to 1 km preserves useful
approximately 3-km structure but does not manufacture 1-km atmospheric information.

### NLDAS-2 baseline plus HRRR anomaly

For a continuous variable `F`, the candidate retrospective hybrid has the conceptual form:

```text
F_hybrid(x, t) = F_NLDAS_downscaled(x, t) + w(x, t) * A_HRRR(x, t)
A_HRRR          = F_HRRR_fine - C_to_fine(F_HRRR_fine)
```

`C_to_fine` means coarsening HRRR to the effective NLDAS/NARR scale and returning it to the
target grid with a documented, conservative or intensive-variable-appropriate operator. Thus
`A_HRRR` carries spatial and temporal structure rather than HRRR's large-scale mean bias. The
weight `w` is fitted only on calibration data and may depend on variable, region, season,
terrain/elevation class, hour, and weather regime. It is zero wherever independent withheld
validation does not show material improvement.

The exact anomaly representation is variable-specific. Temperature may use additive anomalies;
pressure should use log-pressure anomalies; humidity should use bounded RH or vapor-pressure
space; shortwave should preferably use clear-sky index; longwave must remain consistent with
the thermodynamic bundle; and wind uses earth-relative vector-component anomalies. Anomalies
are limited with calibration-derived safeguards, and the coupled temperature-pressure-humidity-
longwave fields must not be independently mosaicked into a physically inconsistent hour.

This hybrid is a calibration target, not yet an enabled production rule. Until it passes
withheld validation, stable retrospective output retains NLDAS-2 and near-real-time output uses
HRRR provisionally. When NLDAS-2 arrives, affected provisional hours are regenerated rather
than permanently splicing the two products at the latency boundary.

### Initial hierarchy by output

| Output | Stable retrospective baseline | Near-real-time/fallback | External constraint or special rule |
|---|---|---|---|
| Precipitation | Conditional MRMS Pass 2/Pass 1, Stage-IV, NLDAS-2, HRRR | Best eligible product available | PRISM daily precipitation |
| Temperature | NLDAS-2 | HRRR analysis | PRISM daily Tmin/Tmax and elevation |
| Pressure | NLDAS-2, paired with temperature | HRRR analysis, paired with temperature | Hydrostatic elevation adjustment |
| Humidity | NLDAS-2, paired with temperature and pressure | HRRR analysis as the same bundle | Preserve RH; recompute specific humidity |
| Shortwave | NLDAS-2 initially | HRRR analysis | Preserve night; evaluate HRRR detail independently |
| Longwave | NLDAS-2, coupled to thermodynamic bundle | HRRR analysis | Cosgrove elevation adjustment |
| Wind U/V | NLDAS-2 baseline plus validated HRRR vector anomalies; NLDAS-2 until validated | HRRR analysis | Rotate vectors correctly; evaluate terrain-flow benefit |

This table is the version-one policy. The three-water-year overlap evaluation may justify
bias-corrected HRRR or a conditional hierarchy for additional variables. Such a change requires
documented independent validation and a production-method version increment.

## Common time, grid, and missing-data conventions

All output times are UTC. Instantaneous fields are labeled by their valid time. Precipitation
is an accumulation over `(T-1h, T]`, labeled by ending time `T`, before conversion to a rate.
PRISM uses 1200-1200 UTC day-ending periods as described in the detailed precipitation and
temperature documents.

Every adapter must normalize:

- Time and, where applicable, interval bounds.
- Units and sign convention.
- Missing values and valid-data masks.
- Native cell centers, cell bounds, projection, and grid-orientation metadata.
- Source product version, analysis/forecast cycle, publication time, and filename.
- Source terrain and its provenance where elevation adjustment is required.

Missing or out-of-domain values remain missing. Zero is a valid physical value for
precipitation, wind components, and shortwave radiation and must never be used as a generic
missing-data replacement.

The extracted NWM grid definition, SCRIP file, active-domain mask, and target elevation are
immutable, fingerprinted production inputs. A changed grid or mask requires new remapping
weights and a new output version.

## Direct spatial remapping

Maintain separate ESMF/CDO weights for every native-grid definition and target-grid revision.
Weight manifests contain hashes of source and target grid descriptions, remapping method,
mask policy, normalization, creation command, and software version.

| Field | Remapping method |
|---|---|
| Precipitation depth | First-order conservative |
| Elevation-normalized temperature | Bilinear |
| Log pressure or hydrostatically normalized pressure | Bilinear |
| Relative humidity | Bilinear |
| Shortwave and longwave radiation | Bilinear |
| Earth-relative U/V wind components | Bilinear |
| Continuous quality indices | Bilinear where meaningful |
| Source IDs, QC flags, and masks | Nearest neighbor or explicit compositing logic |

Conservative remapping is required for precipitation because it is an extensive water amount.
Bilinear interpolation is appropriate for the remaining continuous state or flux fields.
Nearest-neighbor interpolation of a continuous forcing solely to preserve the native value is
not an acceptable default.

## Ordered hourly production pipeline

The order is part of the scientific algorithm:

1. Discover inputs and resolve their revision state.
2. Validate files, coordinates, units, times, masks, and plausible source ranges.
3. Produce preliminary precipitation according to its conditional source workflow.
4. Select one thermodynamic source bundle for temperature, pressure, humidity, and longwave:
   NLDAS-2 retrospectively or HRRR when NLDAS-2 is unavailable.
5. Derive source relative humidity from the bundle's temperature, pressure, and specific
   humidity.
6. Elevation-normalize temperature and pressure, then remap temperature, pressure, and RH to
   the NWM grid.
7. Restore temperature and pressure to NWM target elevation.
8. Apply the PRISM Tmin/Tmax temperature correction when eligible.
9. Recompute specific humidity from remapped RH, final `T2D`, and final `PSFC`.
10. Elevation-adjust longwave consistently with the final thermodynamic state.
11. Remap and validate shortwave radiation.
12. Select, rotate, and remap wind components.
13. Apply the PRISM precipitation constraint when eligible and convert hourly depth to
    `RAINRATE`.
14. Perform cross-variable validation and write the eight forcing fields plus provenance
    atomically.

The bundle rule prevents combinations such as HRRR temperature with NLDAS-2 humidity from
creating artificial relative-humidity jumps. The final humidity calculation must occur after
PRISM temperature correction; otherwise `Q2D` would be inconsistent with the published
temperature.

## Precipitation

Precipitation follows the complete algorithm in
[the precipitation workflow](precipitation_production_workflow.md). In summary:

- Remap all hourly depths conservatively from their native grids.
- Use conditional, quality-aware selection among MRMS Pass 2, MRMS Pass 1, Stage-IV,
  NLDAS-2, and HRRR rather than a fixed national overwrite order.
- Preserve both MRMS passes and distinguish realtime and archive Stage-IV revisions.
- Reconcile complete 1200-1200 UTC daily totals to the appropriate PRISM revision with a
  constrained, nonnegative method that preserves hourly fractions where possible.
- Convert corrected hourly depth to `RAINRATE` only at final output.

Precipitation has its own source identifier because its best source normally differs from the
other meteorological variables.

## Temperature

Temperature follows [the air-temperature workflow](temperature_production_workflow.md). In
summary:

- Use NLDAS-2 as the version-one retrospective hourly shape and HRRR analysis during the
  NLDAS-2 latency gap.
- Normalize from source terrain with a configurable initial lapse rate of
  `gamma = -0.0065 K m-1`, interpolate, and restore to NWM elevation.
- Use PRISM Tmin/Tmax to correct the daily midpoint and range without forcing the arithmetic
  hourly mean to PRISM `tmean`.
- Treat early/provisional PRISM as mutable within the retained NRT stream. Publish stable data
  independently to the retrospective stream rather than replacing NRT output.

The final PRISM-adjusted `T2D`, rather than the preliminary baseline, drives the final humidity
and longwave calculations.

## Surface pressure

### Rationale

Surface pressure is strongly controlled by elevation. Direct interpolation from a coarse grid
to NWM cells would assign pressures representing the wrong terrain. Both NLDAS-2 and HRRR
pressure are tied to their source-grid surface elevation, so they use the same physical
transformation.

Pressure remains paired with the selected thermodynamic source. NLDAS-2 is used for the stable
retrospective record and HRRR fills the latency gap. Switching pressure independently from
temperature and humidity is prohibited in version one.

### Method

Given source pressure `p_s`, source temperature `T_s`, target-adjusted temperature `T_t`, source
elevation `z_s`, target elevation `z_t`, dry-air gas constant `R_d`, gravity `g`, and constant
lapse rate `gamma`, the hydrostatic/ideal-gas adjustment is:

```text
T_t = T_s + gamma * (z_t - z_s)
p_t = p_s * (T_t / T_s) ** (-g / (R_d * gamma))
```

The implementation uses elevation normalization and interpolation rather than applying this
equation only after raw pressure has been interpolated across differing source elevations.
Log-pressure interpolation may be used because pressure varies approximately exponentially
with height. Numerical implementation must handle the near-isothermal limit and reject
nonpositive pressure or temperature.

The pressure adjustment uses the preliminary lapse-adjusted temperature field, not the later
PRISM daily temperature-range correction: PRISM constrains 2-m temperature but does not imply
a corresponding change to the atmospheric mass column. Final humidity nevertheless uses final
PRISM-corrected temperature with this hydrostatically adjusted pressure.

### Diagnostics

Store source and target elevation, elevation difference, unadjusted/remapped pressure,
hydrostatic pressure increment, source ID, and QC flags for implausible inputs or adjustments.

## Specific humidity

### Rationale

Specific humidity cannot be lapse-adjusted independently. A value valid at one temperature and
pressure may imply supersaturation after temperature is lowered at a higher target elevation.
Following Cosgrove et al., relative humidity is assumed constant through the elevation
translation. This preserves atmospheric moisture demand more consistently than directly
interpolating `Q2D`.

### Method

For the selected thermodynamic bundle:

1. Compute saturation vapor pressure `e_s(T_s)`. The production coupled processor uses the
   meteorological RH-over-liquid-water convention at all temperatures, matching NLDAS-2's
   subfreezing humidity behavior; water/ice auto-selection remains available as a lower-level
   diagnostic option.
2. Diagnose vapor pressure and relative humidity from source specific humidity:

   ```text
   e(T, p, q) = q * p / (epsilon + (1 - epsilon) * q)
   RH_s       = e(T_s, p_s, q_s) / e_s(T_s)
   epsilon    = R_d / R_v
   ```

3. Compute saturation specific humidity for source diagnostics:

   ```text
   q_sat(T, p) = epsilon * e_s(T) / (p - (1 - epsilon) * e_s(T))
   ```

4. Apply source QC, then bilinearly interpolate RH to the NWM grid. Do not use unclipped
   supersaturated or negative source values silently.
5. After target-elevation adjustment and PRISM temperature correction, calculate target vapor
   pressure and specific humidity exactly:

   ```text
   e_target = RH_target * e_s(T2D)
   Q2D = epsilon * e_target / (PSFC - (1 - epsilon) * e_target)
   ```

This sequence preserves source RH while ensuring that final temperature, pressure, and
specific humidity are mutually consistent. Small tolerance-scale RH excursions may be clipped;
material excursions are flagged and either rejected or handled by a documented fallback. The
initial operational processor clips and flags source RH through 110% (a fractional tolerance of
0.10) and rejects the complete input hour for any larger excursion; this threshold must be
revisited during calibration.

### Validation

Recalculate RH from final `T2D`, `PSFC`, and `Q2D`. Require finite, nonnegative humidity and RH
within the configured physical tolerance. Compare source and final RH to verify that remapping
and floating-point operations did not introduce unintended changes.

## Downward shortwave radiation

### Rationale and source policy

NLDAS-2 is the initial stable retrospective source because its NARR shortwave field was
bias-corrected using GOES-based Surface Radiation Budget data. HRRR supplies timely analyses and
potentially valuable 3-km cloud structure. Its greater resolution does not by itself establish
lower radiation bias, so a historical switch to HRRR requires independent validation.

Shortwave is not adjusted with the temperature lapse rate. Elevation effects on clear-sky
transmissivity, slope, aspect, and terrain shading are not reliably represented by a simple DEM
correction and are outside version one.

### Method

- Bilinearly remap downward surface shortwave flux directly to the NWM grid.
- Preserve exact source nighttime zeros and enforce zero where target solar geometry places the
  sun below the horizon, allowing a small configurable twilight tolerance.
- Reject negative values beyond numerical tolerance.
- Do not use an interpolation method that spreads daytime flux across the terminator without a
  target-grid solar-geometry check.
- Record whether the source is NLDAS-2 or HRRR and report the source transition.

The implemented hourly processor uses NOAA's fractional-year solar-position approximation on
each NWM cell. Its initial twilight threshold is −0.833° solar elevation, representing the
apparent sunrise/sunset horizon; the threshold is configurable and retained in output
provenance. Negative source values down to `-0.1 W m-2` are treated as numerical noise, clipped,
and flagged. Larger negative values reject the hour. Values over `1400 W m-2` are retained but
flagged for review rather than silently capped.

A later evaluated hybrid may retain NLDAS-2 large-scale bias characteristics while using HRRR
cloud-scale anomalies, preferably in clear-sky-index space. Such a hybrid is not enabled until
it demonstrates improvement against independent radiation observations.

## Downward longwave radiation

### Rationale

Longwave radiation depends on atmospheric temperature, water vapor, and clouds and therefore
belongs to the thermodynamic source bundle. A raw flux interpolated from the wrong terrain
elevation is inconsistent with the adjusted near-surface state. The Cosgrove method uses the
Stefan-Boltzmann relationship to adjust the radiative emission state while retaining source
information about atmospheric emissivity and cloud enhancement.

### Method

1. Select longwave from the same NLDAS-2 or HRRR source as temperature, pressure, and humidity.
2. Diagnose source vapor pressure and the clear-sky emissivity term using the documented
   Cosgrove equations.
3. Diagnose the multiplicative cloud/emissivity adjustment implicit in source `LWDOWN`.
4. Elevation-normalize the radiative temperature/state, bilinearly remap the continuous terms,
   and restore them using final target temperature, pressure, humidity, and elevation.
5. Recalculate target `LWDOWN` through the Stefan-Boltzmann law while retaining the remapped
   source cloud adjustment.

The implementation must reproduce the equations and constants from Cosgrove et al. explicitly;
it must not approximate longwave as `sigma * T2D**4` with unit emissivity. Cloud-factor and
emissivity bounds are configuration parameters established during calibration and must trigger
QC flags when invoked.

For implementation, equations 14-18 reduce to the following source-to-target ratio, with vapor
pressure `e` expressed in hPa, temperature in kelvin, and pressure in Pa:

```text
e       = q * p / (0.622 * 100)
epsilon = 1.08 * (1 - exp(-(e ** (T / 2016))))

L_target = L_source * (epsilon_target * T_target**4) \
                     / (epsilon_source * T_source**4)
```

The Stefan-Boltzmann constant cancels in the ratio. The version-one code follows the paper's
published vapor-pressure approximation here even though the humidity consistency calculation
uses the exact specific-humidity conversion.

Longwave is finalized after PRISM temperature correction and humidity reconstruction. This is
the most internally consistent ordering, but validation must quantify how much the PRISM daily
correction alters hourly longwave flux.

## Wind components

### Rationale and source policy

HRRR's native approximately 3-km terrain and boundary-layer analysis contain more meaningful
near-surface wind structure than the much coarser NARR information underlying NLDAS-2, making
wind one of the strongest candidates for an HRRR refinement. Nevertheless, stable retrospective
production retains NLDAS-2 as its baseline. HRRR vector anomalies receive nonzero weight only
in strata where withheld station and hydrologic validation demonstrates improvement. HRRR is
the direct provisional source during the NLDAS-2 latency window; NLDAS-2 remains the fallback
outside HRRR availability and supplies the consistent long historical record.

Version one does not apply empirical 1-km terrain-speed enhancement, valley channeling, canopy,
or exposure corrections. A DEM alone cannot determine these effects reliably, and an
unvalidated correction could create extreme mountain winds.

### Method

1. Read the GRIB vector-orientation flag and projection metadata; never assume components are
   earth-relative merely from their variable names.
2. Rotate native grid-relative vectors to earth-relative eastward and northward components
   before spatial interpolation.
3. Bilinearly interpolate `u` and `v` components independently. Do not interpolate speed and
   meteorological direction.
4. Write earth-relative `U2D` and `V2D`, matching the sample NWM variables' CF standard names.
5. Reconstruct wind speed and direction for validation only.

Calm winds are valid. Missing components are handled as a missing vector pair; a valid zero
component must not be interpreted as missing. Cells or hours switching to the NLDAS-2 fallback
are flagged, and transition discontinuities are reported.

The implemented HRRR rotation uses the archived grid definition (`LoV=-97.5°`, tangent standard
parallel `38.5°`) to calculate Lambert meridian convergence at every native cell. The rotation
occurs before interpolation, preserving vector meaning. The NLDAS-2 adapter bypasses rotation
because its components are already earth-relative.

## Cross-variable quality control

In addition to variable-specific checks, every output hour must pass:

- Identical time coordinates, NWM grid, active mask, and array shapes for all eight fields.
- Finite values on every active cell unless the publication mode explicitly permits and reports
  gaps.
- `RAINRATE >= 0`, `SWDOWN >= 0`, and physically plausible radiation upper bounds.
- Positive `T2D` in kelvin and positive `PSFC`.
- Nonnegative `Q2D`, internally consistent RH, and no unflagged supersaturation.
- Paired finite `U2D` and `V2D`.
- Nighttime shortwave consistency with target solar geometry.
- Thermodynamic source-bundle consistency.
- Complete source identifiers and QC flags for every active cell.

Spatial seam diagnostics compare neighboring cells with differing sources. Temporal diagnostics
compare each hour with its predecessor and explicitly summarize product transition times. QC
thresholds detect suspect values; they do not silently replace scientifically plausible
extremes.

## Output, provenance, and publication

### Compression and archive layout

HRRR and Stage-IV GRIB conversions are stored as NetCDF4 using shuffle and DEFLATE level 2;
MRMS uses level 4 because its spatially sparse precipitation and quality fields gain materially
more storage reduction for a modest write-time cost. Conversion first produces a private
temporary NetCDF3 file, then `nccopy` creates a
compressed file. Publication is atomic and requires matching dimensions, variable types, and an
exact checksum of all unscaled stored values. `bin/compress_forcing_netcdf.py` applies the same
procedure in place to older files; already-compressed files are skipped, so interrupted migrations
are resumable.

Hourly source files remain the acquisition and late-revision staging unit. Final NWM baseline
forcing production, however, retains only daily collections with one shared coordinate grid.
Each daily task first stages its 24 hourly LDASIN records, atomically publishes and verifies
`YYYY/MM/YYYYMMDD.LDASIN_DOMAIN1`, records cleanup state in the daily manifest, and then removes
the hourly files and manifests. The shorter timestamp distinguishes daily collections from hourly
NWM names; neither uses a `.nc` suffix. Readers continue accepting legacy `.nc` daily files.
This removes repeated latitude/longitude arrays and reduces metadata-server load while keeping
revision rewrites and failure recovery bounded. `bin/submit_forcing_days.py` enables daily-only
publication by default; `--keep-hourly` is a diagnostic escape hatch. Submitted arrays use
descriptive names such as `nwm-baseline-build-20250101-20251231` and
`nwm-baseline-repair-20250812-20250817`; `--job-name` can supply a more specific label (up to 64
characters). Each task requests 120,000 MB of node-local temporary disk by default (`--tmp-mb`),
because one full-CONUS day performs roughly 70-80 GB of scratch writes and CPU-only scheduling can
otherwise pack enough tasks onto one node to trigger CDO/NetCDF HDF write failures. The cluster's
300,000 MB `TmpDisk` nodes can schedule at most two default tasks per node, distributing larger
arrays across additional nodes. PRISM is already daily and
is not included in source-product compaction.

Raw HRRR, MRMS, and Stage-IV source artifacts are retained for a rolling 31-day revision and
recovery window. Older artifacts may be removed only when a verified daily archive records all
24 source files, contains 24 hourly records, and no partially deleted staging set is present.
The cleanup manifest records the daily-file checksum and completed removals so interrupted runs
are resumable. NLDAS-2 and PRISM do not have redundant persistent GRIB copies in this workflow.

Create daily collections with `bin/archive_forcing_daily.py`. Supported collections are `nldas2`, `hrrr`,
`mrms_pass1`, `mrms_pass2`, `mrms_quality`, and the `archive` or `realtime` Stage-IV hourly
stream. A day is published only when all 24 files have identical schemas, a common static grid,
strictly increasing times, and every archived time slice exactly matches its hourly source.
Publication uses an atomic rename and writes a JSON source manifest beside the daily file.
“Daily” describes file granularity, not data timestep. Daily chunks therefore remain in the same
product hierarchy as hourly data: `YYYY/MM/product.YYYYMMDD.nc`, while individual hourly files
remain below `YYYY/MM/DD`. No additional `daily` directory is used.
Daily MRMS chunks also use DEFLATE level 4. HRRR and Stage-IV daily chunks use level 2.
NLDAS-2 daily chunks use level 2 and are stored directly below the year as
`YYYY/NLDAS_FORA0125_H.AYYYYMMDD.020.nc`; their `YYYY/DDD` directories are hourly staging only
and are removed when empty after verified archival.

```bash
python bin/archive_forcing_daily.py hrrr --start 2025-01-01 --end 2025-01-31
python bin/archive_forcing_daily.py nldas2 --start 2025-01-01 --end 2025-01-31
python bin/archive_forcing_daily.py mrms_pass2 --start 2026-07-01 --end 2026-07-31
python bin/archive_forcing_daily.py stage4 --stream archive \
  --start 2026-07-01 --end 2026-07-31
```

Use `--jobs N` to process independent days in isolated worker processes. NetCDF/HDF5 access is
not thread-safe in this environment, so the implementation deliberately uses processes rather
than threads. Size Slurm CPU and memory requests to the worker count; the provided batch entry
point defaults to 32 workers and 64 GB. Reduce the worker count when source fields are larger or
the filesystem is under heavy load.

Under Slurm, large GRIB-conversion, CDO/remapping, and daily-NetCDF construction intermediates
use `/scratch/$SLURM_JOB_USER/job_$SLURM_JOB_ID`. Validated results are copied to a `.part` file
beside the permanent destination and atomically renamed, so readers never see partial products.
Network downloads and the final publication copy remain on permanent storage; moving those to
scratch would add an extra copy without accelerating the network transfer or improving atomicity.

A full-CONUS NWM smoke benchmark (`4445454`) aggregated 9.35 GB of hourly inputs in 4 minutes 32
seconds on a 16-CPU allocation. The verified daily collection occupied about 1.1 GB of allocated
storage and hourly cleanup completed successfully, an approximately 87% disk reduction. Against
the measured 11.66-minute daily production average, aggregation raises expected wall time to about
16 minutes per day, or roughly 39%. This cost is accepted because it substantially reduces both
capacity use and metadata load; remeasure under high concurrency because the shared filesystem is
the scaling limit. A later PRISM-constrained pass currently spends about eight additional minutes
extracting 24 records from compressed daily baselines to scratch (16:16 archive-input versus 8:30
hourly-input in the controlled test). Thus a baseline-plus-one-constraint pass is approximately
32 minutes rather than 20 minutes per day, about 60% slower end to end. Direct time-slice reads
from the daily NetCDF collection are the next optimization target; daily-only retention does not
preclude that improvement.

Hourly deletion is opt-in with `--delete-hourly` and is refused inside the configurable
`--minimum-age-days` window, which defaults to 31 days. `bin/cleanup_archived_forcing.py` removes
older hourly and raw artifacts only after the verified daily-archive checks above; its default is
a non-destructive report and deletion additionally requires `--apply`.

The primary forcing file follows the target NWM variable names, dimensions, units, coordinates,
fill value, and compression/chunking conventions. It contains exactly the expected time steps
for its publication interval. A sidecar provenance file or diagnostic group contains at least:

- Per-variable source IDs and QC bit masks.
- Input file identifiers, checksums or stable fingerprints, and product revisions.
- Analysis cycles and forecast hours where applicable.
- PRISM releases and correction diagnostics.
- Source and target terrain identifiers and elevation-adjustment diagnostics.
- Remapping-weight fingerprints and software versions.
- Production-method/configuration version and run timestamp.
- Counts of missing values, fallbacks, caps, rejected constraints, and source transitions.

Write data to a temporary path, validate the complete artifact, and publish by atomic rename.
Revisions create versioned artifacts or manifests; they are never overwritten without trace.

## Operational revision states

### Near-real-time

- Use the best eligible precipitation product currently available.
- Use HRRR for the thermodynamic bundle until NLDAS-2 arrives.
- Use HRRR wind.
- Apply early/provisional PRISM only to complete PRISM days.
- Rebuild affected intervals when MRMS Pass 2, Stage-IV revisions, NLDAS-2, or revised PRISM
  becomes available.
- Label the output mutable and report its latest complete hour.

### Stable retrospective

- Use final eligible precipitation sources and stable PRISM precipitation.
- Use NLDAS-2 as the large-scale baseline for temperature, pressure, humidity, radiation, and
  wind under the version-one policy, except documented gaps.
- Add calibrated HRRR mesoscale anomalies only for variable/region/season/regime strata that
  improve independent withheld validation; otherwise the anomaly weight is zero.
- Apply stable PRISM Tmin/Tmax.
- Publish only after complete-interval and cross-variable validation passes.

## Calibration and independent evaluation

The initial common study period is water years 2023-2025, from 2022-10-01 through 2025-09-30.
All seasons, elevation bands, climate regions, and day/night regimes must be represented.

Use either leave-one-water-year-out cross-validation or the initial split:

- WY2023-WY2024: development and calibration.
- WY2025: untouched independent temporal evaluation.

Where station coverage permits, also withhold spatial regions or station groups. Candidate
evaluation data include quality-controlled surface meteorological networks for temperature,
humidity, pressure, and wind; SURFRAD or comparable radiation observations; precipitation
gauges; and basin-scale hydrologic diagnostics. Calibration targets include lapse rate,
temperature range safeguards, RH tolerance, longwave emissivity/cloud-factor bounds, radiation
transition handling, and any future HRRR bias correction.

Assess NLDAS-2 and HRRR by variable, season, elevation, terrain class, region, and hour of day.
Metrics include bias, MAE, RMSE, correlation, diurnal amplitude/phase, distribution tails,
source-transition jumps, and relevant conservation or physical-consistency errors. A finer grid
is adopted as the preferred source only when independent evaluation demonstrates material
benefit without unacceptable discontinuities.

HRRR calibration is version-aware. The primary homogeneous archive begins with the first full
UTC day under HRRRv4, 2020-12-03; the 2020-12-02 transition day is excluded from homogeneous
training. Major HRRR versions must be recorded as separate strata and must not be pooled as one
stationary record. Backfilling older versions can support rare-event sensitivity work, but is
not needed to extend the core retrospective forcing because NLDAS-2 already supplies the long
baseline. The HRRRv4 backfill therefore has first priority.

The principal comparison is (A) elevation-downscaled NLDAS-2, (B) bias-corrected HRRR, and
(C) NLDAS-2 plus HRRR mesoscale anomalies. Evaluation uses calibration/withheld splits and
independent stations and radiation networks, stratified by region, season, elevation, terrain,
and event type. Hydrologic and snow-state verification is required in addition to meteorological
scores before a refinement becomes operational.

The daily PRISM AN archive begins 1981-01-01. Historical backfill therefore acquires `ppt`,
`tmin`, `tmax`, and derived `tmean` from that date onward at 4-km resolution. Stable historical
grids are downloaded once, while the normal updater continues checking the rolling six-month
mutable window.

For 1979-1980, NLDAS-2 remains the hourly source and PRISM AN monthly `ppt`, `tmin`, and `tmax`
provide lower-frequency constraints. This is a distinct, explicitly labelled forcing tier; a
monthly grid must never be copied or interpolated into synthetic daily PRISM grids. Monthly
precipitation constrains the sum of the hourly sequence over each calendar month while retaining
NLDAS-2 event timing. Monthly `tmin` and `tmax` constrain, respectively, the monthly mean of the
daily hourly minima and maxima. A spatially varying affine shift/range correction preserves each
cell's NLDAS-2 submonthly and diurnal evolution. Ratio/scale bounds, missing-target masks, and
correction diagnostics are required just as for the daily constraint. The resulting provenance
must identify `prism_constraint_frequency=monthly` so it cannot be confused with the daily
1981-present retrospective product.

An overlap check using all twelve months of 1981 found that monthly PRISM precipitation agrees
with the sum of daily PRISM to about 0.002 mm RMSE. Monthly temperature is not merely the
arithmetic aggregation of the downloaded daily grids: monthly-versus-daily-derived RMSE ranges
from about 0.45-0.60 degC for Tmin and 0.37-0.63 degC for Tmax. The monthly temperature grids are
therefore treated as authoritative independent monthly targets rather than reconstructed daily
constraints. This validation should be rerun if PRISM changes either archive version.

The implemented historical-month processor is `bin/produce_prism_constrained_month.py`. It uses
a two-pass, daily-archive workflow: the first pass accumulates monthly precipitation and the
monthly means of each day's hourly Tmin/Tmax; the second applies one bounded precipitation
factor and one affine temperature correction per grid cell while copying each daily archive.
Relative humidity is preserved through the temperature adjustment by recomputing `Q2D`, and
`LWDOWN` is reconstructed with the existing Cosgrove atmospheric-emission factor. Thus no full
month of CONUS hourly fields is held in memory and no intermediate hourly files are published.
The output remains one suffix-free `YYYYMMDD.LDASIN_DOMAIN1` file per calendar day, accompanied
by one monthly diagnostic file. Every output records `prism_constraint_frequency=monthly` and
the three source grids.

Monthly publication has four fractional precipitation gates plus an hourly-extreme guard. The fractions of constrained
PRISM cells that are numerically unconverged, materially unresolved, ratio/depth capped, or have
a wet PRISM target over a dry baseline must not exceed 0.5%, 0.5%, 2%, and 0.5%, respectively.
The unresolved fraction is evaluated from `abs(residual) / max(PRISM, 1 mm)` using the solver
tolerance. A cell excluded from numerical iteration because a correction is physically
infeasible therefore cannot disappear from the publication decision. Corrected precipitation
must also remain at or below 300 mm in every grid cell and hour. All observed values and limits
are stored in the monthly diagnostics and final daily global attributes.

An initial January 2021 diagnostic based on the modern combined precipitation baseline failed
the gates. Only
0.2365% of constrained cells carried the numerical `NOT_CONVERGED` flag, but 7.4470% remained
materially unresolved, 7.8108% encountered the 10x correction bound, and 1.0099% had a wet target
over a dry baseline. Uncapped feasible cells reproduced PRISM well (0.036 mm RMSE), while capped
cells delivered a median of only 3.5% of their target. A provenance audit showed that these cells
were dominated by MRMS-selected near-zero precipitation. That modern combined-source result is
therefore retained as a rejected test, but it is not representative of the NLDAS-2-only
1979-1980 baseline.

The production-representative control remapped NLDAS-2 alone for January 2021. With a 10x bound,
0.5178% remained unresolved, narrowly exceeding the 0.5% gate. A 100x bound passed every gate:
0.0006% was numerically unconverged, 0.2280% unresolved, 0.5218% capped, and 0.2122% had a wet
target over a dry baseline. Although the allowed ceiling is 100x, 99.9% of finite correction
factors were below 13.9x. The smallest tested passing bound, 100x, is therefore used together
with the independent 300 mm/hour publication guard. The NWM-to-PRISM conservative weights also
exclude inactive NWM cells at generation time; the accepted operator contains no links from
inactive source cells.

NLDAS-2 begins at 13 UTC on 1979-01-01. January 1979 cannot form a complete calendar-month
constraint and is intentionally excluded; unconstrained daily baseline production begins on
1979-01-02, and monthly constrained production covers 1979-02 through 1980-12.

For example, after unconstrained daily forcing exists below `outputs/forcing/nwm/baseline`, run:

```bash
python bin/produce_prism_constrained_month.py \
  --year 1979 --month 2 \
  --complete-root outputs/forcing/nwm/baseline \
  --output-root outputs/forcing/nwm/retro \
  --maximum-ratio 100
```

### Implemented experimental hybrid

The target-grid hybrid machinery is implemented but disabled by default. Both sources first
pass independently through their native-grid normalization, terrain adjustment, vector
rotation, and direct NWM-grid remapping. A NaN-aware 33-cell box smoother then estimates the
coarse HRRR component on the 1-km grid, and the residual supplies the candidate mesoscale
anomaly. The initial 33-km window is configurable and must be calibrated; it is not a claim that
all NLDAS variables have one exact effective resolution.

The implemented anomaly spaces are:

| Field | Anomaly space and reconstruction |
|---|---|
| Temperature | additive kelvin anomaly |
| Pressure | additive log-pressure anomaly, then exponentiation |
| Humidity | additive bounded relative-humidity anomaly, then recompute specific humidity |
| Longwave | additive log anomaly of the Cosgrove longwave factor, then reconstruct from final T/P/RH |
| Shortwave | additive clear-sky-index anomaly using target solar geometry; preserve night zero |
| Wind | additive earth-relative U and V anomalies |

Every weight is constrained to `[0, 1]`, and variable-specific anomaly caps set QC bits when
invoked. Output provenance records the baseline and HRRR components, all weights, and smoothing
window. A hybrid hour requires both sources; it fails explicitly rather than silently dropping
an enabled refinement. With all weights at their default zero, the normal NLDAS-first workflow
is unchanged and does not incur the second HRRR remapping.

Real-overlap testing found rare HRRR cells with diagnosed RH between 110% and 116% when the
common over-liquid-water convention is applied at all temperatures (3 of about 1.9 million
finite source cells exceeded 110% in the initial test hour). HRRR therefore uses a provisional
20% supersaturation tolerance: accepted excursions are clipped to saturation and flagged.
Larger isolated HRRR outliers are masked and the hybrid retains NLDAS-2 locally rather than
rejecting the national hour. NLDAS-2 retains the strict 10% whole-input threshold. This
source-specific policy is subject to calibration review.

For development-only experiments, `bin/produce_forcing_hour.py` accepts separate thermodynamic,
radiation, and wind weights. For example:

```bash
python bin/produce_forcing_hour.py YYYYMMDDHH ... \
  --hybrid-temperature-weight 0.25 \
  --hybrid-pressure-weight 0.25 \
  --hybrid-humidity-weight 0.25 \
  --hybrid-longwave-weight 0.25 \
  --hybrid-shortwave-weight 0.25 \
  --hybrid-wind-weight 0.25 \
  --hybrid-window-cells 33
```

These values are diagnostic, not production recommendations. PRISM temperature reconciliation
must operate on the complete 24-hour hybrid sequence so daily extrema and thermodynamic
reconstruction remain correctly ordered. The single-hour command therefore rejects simultaneous
hybrid weights and a precomputed final-temperature constraint.

The implemented daily two-pass driver uses the half-open PRISM day
`[D-1 day 12:00 UTC, D 12:00 UTC)`. It first produces all 24 unconstrained hybrid hours. It then
remaps PRISM Tmin/Tmax to the NWM grid, applies the affine correction to the complete hybrid
temperature curve, and revises each hour. Relative humidity is diagnosed from the preliminary
`T2D/Q2D/PSFC`; final `Q2D` is recomputed at corrected temperature while preserving RH, and
`LWDOWN` is reconstructed while preserving the hour's Cosgrove longwave factor. Pressure is not
changed by PRISM because the daily surface-temperature constraint does not imply a change in
atmospheric column mass. Precipitation is composited afterward, and the 24 complete hourly files
are also written as one checksum-verified daily LDASIN collection.

`slurm/produce_hybrid_prism_hour.py` supplies the parallel preliminary-hour array and
`bin/produce_prism_constrained_day.py` performs the dependent constraint, reconstruction,
precipitation, and daily publication stage.

The first complete full-CONUS test used PRISM day 2023-01-22 and experimental 0.25 HRRR
weights. It published 24 complete hourly files plus a 24-record daily file. About 7.36 million
cells received an unconstrained affine Tmin/Tmax match; about 135 thousand valid-PRISM cells
invoked the configured temperature-range scale cap, so their extrema intentionally do not match
PRISM exactly. Cells without a valid PRISM constraint retained the hybrid baseline and were
flagged. This confirms the guardrails operate as designed; the 0.25 weights and scale bounds
remain experimental rather than calibrated production values.

A January 2026 scaling test separated precipitation production from PRISM temperature
revision. Reusing complete hourly LDASIN files reduced a temperature-only PRISM revision from
82 minutes to 11 minutes while preserving all precipitation fields exactly. Daily batched
precipitation processing then normalized 24 hours per available product and applied each static
CDO operator once per product, rather than once per product per hour. For PRISM day 2026-01-20,
the four precipitation candidates plus MRMS quality required five remap calls instead of 120,
completed in 8 minutes 28 seconds, and reproduced all 24 hourly `RAINRATE`, source ID,
confidence, QC, and mask fields exactly. Production integration should retain an hourly fallback
for days whose candidate availability changes within the 24-hour window.

The operational constraint driver now combines both corrections in one scratch-backed task and
publishes the final daily LDASIN collection directly. A full-CONUS test for PRISM day 2026-07-15
completed in 8 minutes 30 seconds with 12 allocated CPUs, including PRISM Tmin/Tmax preparation,
temperature/humidity/longwave reconstruction, bounded precipitation reconciliation, diagnostics,
and atomic daily publication. Its sample hours matched the independently produced temperature
and precipitation reference results. The driver accepts either persistent hourly LDASIN files or
daily baseline collections; for the latter it locates records by their actual NetCDF time values,
extracts them only to node-local scratch, and removes them at task completion.

A rolling scheduler classifies PRISM days as early, provisional, or stable and checks that the
PRISM inputs and time-addressable baseline records exist. PRISM's physical 1200-1200 UTC
constraint window remains an internal scientific interval, never a production file boundary.
`bin/submit_prism_calendar_batches.py` groups missing UTC dates into bounded contiguous batches.
Each worker keeps only two adjacent PRISM constraint windows on node-local scratch, publishes the
corresponding `00-23 UTC` calendar file atomically, validates it, and advances by one day. Scratch
cleanup removes the internal windows when the job exits. Early and provisional output is retained
in the `nrt` stream; stable output is published independently in `retro`. Separate
concurrency-bounded SLURM arrays and version-controlled cron entries prevent stable publication
from replacing the near-real-time record.

The canonical on-disk hierarchy is:

```text
outputs/forcing/nwm/baseline/        unconstrained reusable baseline
outputs/forcing/nwm/nrt/             retained early/provisional operational record
outputs/forcing/nwm/retro/           stable daily or historical-month retrospective record
```

Both published streams use `YYYY/MM/YYYYMMDD.LDASIN_DOMAIN1` below their root. Stream schedulers
derive these roots from `--stream`; an explicit override must end in `/nrt` or `/retro` as
appropriate, and both the submitter and worker reject crossed paths. Retrospective publication
never searches the NRT tree for an output to replace. The baseline tree is source material, not
a third published quality tier.

Baseline retention is transitional. After stable retrospective output is accepted, the baseline
archive is deleted to avoid retaining a second multi-terabyte copy. Cleanup is deliberately
coverage-aware: parallel calendar publishers never delete baseline input because adjacent batches
temporarily share boundary dates. After the complete controller array converges, a separate serial
audit removes UTC baseline day `D` only when the stable, accepted retro file for that date contains
all 24 records and no active controller can still depend on it. A failed batch is resumed by
rerunning the idempotent submitter, which forms new contiguous batches from the remaining baseline
inventory. For the 1979-1980 monthly
method, a baseline month is removed only after its monthly diagnostic is accepted and every
calendar-day retro file is present. Missing, provisional, rejected, or partial retro coverage
always retains the baseline. The independent audit/cleanup command remains
`bin/cleanup_stable_baseline.py`.

Operational scheduling separates prompt updates from deeper revision repair:

`bin/update_nwm_forcing.py` is the single operational entry point. It records a run manifest,
submits source refreshes, attaches baseline work to those downloads, and launches the calendar-day
PRISM continuation only after its prerequisites leave the queue. NRT cycles retain baseline.
Monthly retro cycles build only the three-day dependency halo around missing stable targets and
attach a serial stable-baseline cleanup after successful PRISM completion. Existing accepted retro
dates are terminal and do not cause baseline reconstruction.

```bash
python bin/update_nwm_forcing.py --cycle six-hourly
python bin/update_nwm_forcing.py --cycle daily
python bin/update_nwm_forcing.py --cycle monthly-retro
```

- Run a 10-day NRT scan every six hours. This covers Stage-IV regeneration during its first day
  and at approximately 1, 3, 5, and 7 days, plus the usual 3-4-day NLDAS-2 latency and PRISM's
  first and five-day runs.
- Run a 200-day NRT scan once daily. This covers PRISM's full rolling six-month mutable period,
  with a buffer for delayed downloads and missed schedules.
- Run a 45-day retrospective scan monthly on the 18th, after PRISM's usual mid-month modeling
  cycle. The scheduler offsets this window to end 183 days behind the current date, so it targets
  newly stable days rather than recent NRT dates. Stable outputs that are already current are
  skipped.
- Periodically audit the complete retrospective inventory separately. This is a completeness
  check, not a reason to regenerate unchanged stable forcing.

PRISM daily grids are produced about one day and five days after the target day, then approximately
monthly through the final six-month run. They are daily products, but their later provisional and
stable updates follow this monthly modeling cycle. These rules are encoded in the scheduler and
the canonical cron file rather than relying on operator memory.

The complete archive-only operational path was subsequently tested for 2026-07-15. Two verified
calendar-day baseline collections were produced in parallel in 5 minutes each, then exposed to
the scheduler in an isolated root containing no hourly LDASIN files. The scheduler selected
exactly the one complete PRISM day and submitted job 4439541_0 with the expected provisional
revision. The initial implementation extracted 24 compressed records to scratch and completed in
16 minutes 16 seconds on 12 allocated CPUs with 20.3 GB peak memory.

Job 4445630 repeated that full-CONUS case by passing each daily archive and its NetCDF time-record
index directly through temperature adjustment, precipitation reconciliation, and final daily
publication. It completed in 10 minutes 57 seconds on 12 CPUs with about 3.4 GB peak memory: 5
minutes 19 seconds (32.7%) faster than extraction and only 2 minutes 27 seconds slower than the
8-minute-30-second hourly-input reference. Dimensions, variables, and time coordinates matched;
all eight forcing fields matched exactly over three separated 256-by-256 windows for all 24
hours; and every complete PRISM diagnostic field matched exactly. Synthetic tests additionally
verify exact arbitrary-record selection and publication. Direct indexed access is therefore the
default; `--archive-access extract` remains available as a diagnostic fallback.

## Implementation phases

1. Complete and inventory the common NLDAS-2, HRRR, and PRISM archive; acquire static native
   terrain for each grid definition.
2. Implement shared source adapters, unit/time normalization, masks, grid fingerprints, and
   provenance manifests.
3. Generate and validate direct native-to-NWM remapping weights.
4. Implement thermodynamic primitives: saturation vapor pressure, RH conversion, lapse-rate
   temperature, hydrostatic pressure, and Cosgrove longwave adjustment.
5. Implement the coupled temperature-pressure-humidity-longwave pipeline and PRISM temperature
   constraint.
6. Implement shortwave remapping, solar-geometry QC, wind rotation/remapping, and source
   transition diagnostics.
7. Implement precipitation compositing and PRISM reconciliation according to its detailed
   workflow.
8. Assemble complete NWM forcing files with cross-variable QC and atomic publication.
9. Run calibration and withheld validation; freeze version-one parameters and decision rules.
10. Add rolling near-real-time revision and stable retrospective production schedules.

## Current implementation status

The design above is the production target. As of 2026-08-25, the following foundations are
implemented and tested:

- Extraction of the NWM target grid and SCRIP description from the sample LDASIN file.
- Chunked sampling of GMTED2010 onto the active NWM grid, producing the reusable target
  elevation file.
- Acquisition of NASA's authoritative NLDAS mean-elevation grid.
- Extraction of native HRRR `HGT:surface:anl` and preservation of its complete GRIB grid
  description. Inspection confirms that HRRR U/V records are grid-relative.
- Structural validation and coordinate fingerprinting for individual NLDAS-2, HRRR, and PRISM
  files.
- Fast date-range completeness reports for NLDAS-2, HRRR, and PRISM variables.
- Lazy normalized source readers with canonical variable names and units. HRRR longitudes are
  normalized to `[-180, 180)` while its wind orientation remains explicitly grid-relative.
- Unit-tested fixed-lapse-rate temperature, hydrostatic pressure, exact humidity/RH conversion,
  PRISM Tmin/Tmax curve adjustment, vector rotation, and Cosgrove longwave primitives.
- Direct NLDAS-2, HRRR, and PRISM bilinear weights plus conservative precipitation weights,
  each with a source/target grid-fingerprint manifest. HRRR conservative weights use native
  GRIB geometry because the wgrib2 NetCDF conversion lacks cell corners.
- Full-grid NLDAS-2 and HRRR temperature remapping smoke tests on the 17,694,720-cell NWM grid.
- A coupled hourly temperature-pressure-humidity-longwave processor that moves the four-field
  state to a common reference elevation before one bilinear remap, restores target elevation,
  optionally accepts final PRISM-constrained temperature, reconstructs humidity and longwave,
  applies the NWM mask, and writes provenance/QC diagnostics atomically. A full-grid NLDAS-2
  smoke test completed on 10,762,839 active cells, and a projected-grid HRRR smoke test
  completed on 10,322,743 covered active cells.
- An hourly shortwave/wind processor that rotates HRRR grid-relative vectors before bilinear
  remapping, preserves NLDAS-2 earth-relative vectors, enforces target-cell nighttime shortwave
  zeros, pairs missing wind components, and writes time/provenance/QC diagnostics atomically.
  Full-grid NLDAS-2 and HRRR smoke tests completed with no positive shortwave below the
  configured solar horizon.
- Exact-time whole-hour source selection that prefers structurally valid NLDAS-2 and falls back
  to HRRR without mixing thermodynamic, radiation, or wind products within an hour.
- Experimental NLDAS-2-plus-HRRR target-grid hybrid transformations for all non-precipitation
  fields. Zero weights reproduce the NLDAS-2 baseline; nonzero weights and the smoothing scale
  remain uncalibrated and are not enabled in routine production.
- Runtime rejection of stale bilinear weights using source and NWM-grid fingerprints, plus
  terrain coverage/range validation and SHA-256 provenance for source and target elevations.
- Elevation-aware PRISM Tmin/Tmax preparation and guarded 24-hour affine reconciliation. A real
  PRISM day remapped successfully with no Tmin/Tmax inversions; incomplete PRISM coverage
  retains the hourly baseline and is flagged.
- Atomic seven-field LDASIN assembly matching the sample variable names, dimensions, bounds,
  units, and fill value. This reusable intermediate deliberately marks `RAINRATE` pending until
  the precipitation component is atomically added; a real NLDAS-2 hour produced successfully.
- Quality-aware precipitation adapters and deterministic target-cell selection across MRMS
  Pass 2/Pass 1, Stage-IV archive/realtime, NLDAS-2, and HRRR. Negative MRMS no-coverage codes
  remain missing, and outputs retain confidence, source IDs, QC, and exact hourly bounds.
- Fingerprinted MRMS and Stage-IV conservative weights and MRMS-quality bilinear weights. A
  real five-source hour filled all active NWM cells without negative precipitation.
- Atomic complete eight-variable LDASIN publication, resumable range production, rolling SLURM
  arrays, per-hour manifests, and a version-controlled six-hour cron submission entry.
- Reusable continuous/stratified metrics and MRMS threshold-sweep scaffolding. These tools do
  not constitute calibration results while the archive and independent references are incomplete.
- Bounded conservative PRISM daily precipitation reconciliation using a sparse NWM-to-PRISM
  operator, with explicit zero/missing cases, synthetic-timing flags, correction caps,
  residuals, and atomic revision-labeled output.
- Deterministic whole-storm/day calibration-withheld splitting, categorical precipitation
  scores, and regional Stage-IV override sweeps. Rule selection uses calibration samples only;
  the selected rule is subsequently scored on withheld samples.
- Direct daily publication of the combined PRISM temperature and precipitation corrections,
  including daily-baseline input support that avoids retaining persistent hourly LDASIN files.
- Revision-aware rolling PRISM scheduling with completeness, staleness, revision-transition,
  active-job, and bounded-concurrency guards plus a version-controlled cron entry.

Generated static and remapping artifacts live below `data/static` and are intentionally ignored
by Git; the scripts, tests, and documentation required to recreate them are tracked.

The following production components remain incomplete and must not be inferred from the
implemented primitives:

- Generation and full-grid validation of the reverse NWM-to-PRISM conservative operator,
  followed by an archive-scale PRISM reconciliation run.
- Assembly of genuinely independent gauge and basin samples, execution of the implemented
  calibration/withheld workflow, transition diagnostics, and freezing of production thresholds.

The WY2023-WY2025 source backfill is still running. Implementation and synthetic/short-window
testing can continue during the backfill, but calibration claims require a complete audited
archive.

## References

- Cosgrove, B. A., et al. (2003), *Real-time and retrospective forcing in the North American
  Land Data Assimilation System (NLDAS) project*,
  <https://doi.org/10.1029/2002JD003118>.
- NASA NLDAS-2 forcing dataset information,
  <https://ldas.gsfc.nasa.gov/nldas/v2/forcing>.
- NOAA HRRR documentation, <https://rapidrefresh.noaa.gov/hrrr/>.
- NOAA RAP/HRRR near-surface diagnostic variable documentation,
  <https://rapidrefresh.noaa.gov/RAP_var_diagnosis.html>.
- PRISM dataset documentation,
  <https://www.prism.oregonstate.edu/documents/PRISM_datasets.pdf>.
- PRISM time-series revision policy, <https://prism.oregonstate.edu/data/>.
