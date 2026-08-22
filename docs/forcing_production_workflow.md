# NWM 1-km meteorological forcing production workflow

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
NLDAS-2 is delayed, and it is the preferred wind source during the modern HRRR archive.

### Initial hierarchy by output

| Output | Stable retrospective baseline | Near-real-time/fallback | External constraint or special rule |
|---|---|---|---|
| Precipitation | Conditional MRMS Pass 2/Pass 1, Stage-IV, NLDAS-2, HRRR | Best eligible product available | PRISM daily precipitation |
| Temperature | NLDAS-2 | HRRR analysis | PRISM daily Tmin/Tmax and elevation |
| Pressure | NLDAS-2, paired with temperature | HRRR analysis, paired with temperature | Hydrostatic elevation adjustment |
| Humidity | NLDAS-2, paired with temperature and pressure | HRRR analysis as the same bundle | Preserve RH; recompute specific humidity |
| Shortwave | NLDAS-2 initially | HRRR analysis | Preserve night; evaluate HRRR detail independently |
| Longwave | NLDAS-2, coupled to thermodynamic bundle | HRRR analysis | Cosgrove elevation adjustment |
| Wind U/V | HRRR analysis wherever archived | HRRR analysis | NLDAS-2 fallback; rotate vectors correctly |

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
- Treat early/provisional PRISM as mutable and regenerate when stable data arrive.

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

HRRR is the initial preferred source wherever its modern archive is available. Its native
approximately 3-km terrain and boundary-layer analysis contain more meaningful near-surface
wind structure than the much coarser NARR information underlying NLDAS-2. NLDAS-2 remains the
fallback outside HRRR availability and supplies a consistent long historical record.

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
- Use NLDAS-2 for temperature, pressure, humidity, and radiation under the version-one policy,
  except documented gaps.
- Use HRRR wind where the accepted HRRR archive is available and NLDAS-2 otherwise.
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

The design above is the production target. As of 2026-08-22, the following foundations are
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

Generated static and remapping artifacts live below `data/static` and are intentionally ignored
by Git; the scripts, tests, and documentation required to recreate them are tracked.

The following production components remain incomplete and must not be inferred from the
implemented primitives:

- Rolling multi-day scheduling and revision-aware replacement around the implemented PRISM
  constraint and hourly production commands.
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
