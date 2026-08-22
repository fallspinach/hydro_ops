# NWM 1-km air-temperature production workflow

## Purpose

This workflow produces hourly 2-m air temperature forcing on the National Water Model (NWM)
CONUS 1-km grid. NLDAS-2 supplies the preferred retrospective hourly evolution, HRRR analysis
fills the recent NLDAS-2 latency gap, and PRISM daily minimum and maximum temperature constrain
the daily level, range, and broad spatial pattern.

The workflow has four objectives that must be satisfied together:

1. Preserve a physically plausible hourly diurnal cycle.
2. Match PRISM daily minimum and maximum temperature when an appropriate PRISM revision is
   available.
3. Account explicitly for elevation differences among the source grids, PRISM, and the NWM
   grid.
4. Retain enough provenance to identify the hourly baseline, terrain adjustment, PRISM
   correction, and revision state at every target cell and hour.

Temperature is remapped directly from each native grid to the NWM grid. There is no
intermediate 4-km production grid.

## Roles of the input products

The products play complementary roles rather than forming a universal accuracy ranking:

- **NLDAS-2** provides a spatially complete, hourly, observation-informed forcing record. Its
  approximately 0.125-degree grid is too coarse to define 1-km terrain structure, but it is the
  preferred retrospective baseline so the historical record has a consistent hourly source.
- **HRRR** provides timely 3-km analysis fields and fills the most recent days before NLDAS-2
  becomes available. It has finer spatial detail, but changing between two modeling systems can
  introduce a discontinuity. HRRR-derived hours are therefore provisional and are replaced by
  NLDAS-2 in the stable retrospective product unless evaluation supports a different policy.
- **PRISM AN** daily temperature uses station observations and physiographically informed
  interpolation. Daily minimum and maximum temperature constrain the baseline's daily midpoint,
  amplitude, and spatial biases. PRISM does not provide the timing of the hourly diurnal cycle.
- **Terrain elevation** supplies the static information needed to translate temperatures between
  the elevations represented by each source and the NWM grid.

NLDAS-2 is preferred historically for consistency, not because it is assumed to outperform
HRRR everywhere. Overlap evaluation against independent stations should determine whether later
versions use HRRR in additional regions, seasons, or weather regimes.

## Variables, units, and time conventions

The internal and output quantity is 2-m air temperature in kelvin. Source values in degrees
Celsius are converted using:

```text
T[K] = T[degC] + 273.15
```

Hourly fields represent an instantaneous or analyzed value at the labeled UTC time; they are
not hourly averages. The implementation must preserve source time semantics in metadata.

PRISM uses a day-ending convention. A PRISM grid labeled date `D` represents the period:

```text
(D - 1 day) 12:00 UTC through D 12:00 UTC
```

The boundary convention for selecting hourly samples must be defined once and tested so that a
sample exactly at 1200 UTC is not assigned to two days. PRISM `tmean` is derived as:

```text
tmean = (tmax + tmin) / 2
```

It is not the arithmetic mean of 24 hourly temperatures. Consequently, `tmin` and `tmax` are
the primary constraints; `tmean` is retained as a consistency check and must not be used as a
constraint on the hourly arithmetic mean.

## Required source data

### NLDAS-2

- NASA GES DISC `NLDAS_FORA0125_H.2.0` hourly primary forcing (File A).
- Extract 2-m air temperature and its time coordinate from every hourly file.
- Retain source filenames, version identifiers, publication/modification times, native-grid
  coordinates, masks, and units.
- Obtain the terrain elevation associated with the NLDAS grid. Prefer an authoritative native
  NLDAS terrain field. If one cannot be obtained, aggregate the common GMTED2010 DEM to the
  NLDAS grid and record that substitution explicitly.

### HRRR

- Use `TMP:2 m above ground:anl` from the `f00` surface analysis valid at the target hour.
- Retain the native Lambert conformal grid and analysis-cycle metadata.
- Obtain `HGT:surface` for each distinct HRRR grid definition as static grid metadata. It need
  not be downloaded in every hourly forcing subset. If native HRRR terrain is unavailable,
  aggregate GMTED2010 to the HRRR grid and flag the substitution.

### PRISM

- Download AN 4-km daily `tmin`, `tmax`, and `tmean` NetCDF products.
- Treat `tmin` and `tmax` as the correction constraints and `tmean` as a validation field.
- Retain release date, grid count, revision metadata, source filename, mask, units, and native
  grid coordinates.
- Use the elevation represented by PRISM when lapse-normalizing PRISM temperatures. Prefer the
  elevation grid used by PRISM if available; otherwise aggregate the common GMTED2010 DEM to
  the PRISM grid and document the approximation.

### NWM target grid and DEM

- Use the extracted NWM `LDASIN_DOMAIN1` target-grid definition and SCRIP file.
- Use the GMTED2010 mean-elevation grid already acquired at 30 arc-seconds to derive a target
  elevation for every active NWM forcing cell.
- Apply the NWM land/domain mask consistently to values, terrain, provenance, and QC fields.

## Baseline source selection

For every target hour, select one complete hourly baseline before applying PRISM constraints:

1. Use valid NLDAS-2 where it is available.
2. Use valid HRRR analysis where NLDAS-2 has not yet been published or is missing.
3. Leave the result missing if neither source is valid; do not silently temporal-fill gaps in
   the first implementation.

Selection occurs by target cell after remapping so valid source coverage can be respected.
Ordinarily one product should cover the full CONUS domain at an hour. Mixed-source hours must be
flagged and checked for spatial seams.

Near the NLDAS-2/HRRR time boundary, compare overlapping hours and report the source difference.
The initial implementation should rely on the same elevation and PRISM corrections for both
products and replace HRRR days atomically when NLDAS-2 arrives. If testing still finds an
operationally important step change, introduce a documented overlap bias correction or short
transition blend without altering the stable retrospective hierarchy.

## Elevation adjustment and spatial remapping

### Fixed-lapse-rate transformation

Use the Cosgrove et al. fixed environmental lapse rate for the first implementation:

```text
gamma = -0.0065 K m-1
T_target = T_source + gamma * (z_target - z_source)
```

Thus a target cell above the elevation represented by the source is cooler. Elevation
differences and the applied increment must be retained as diagnostics.

The calculation should be implemented as elevation normalization followed by interpolation:

```text
T_reference = T_source + gamma * (z_reference - z_source)
T_target    = interpolate(T_reference) + gamma * (z_target - z_reference)
```

Using a common reference elevation of zero simplifies this to interpolation of a sea-level-
equivalent field followed by restoration to target elevation. This avoids interpolating
temperatures that represent different elevations. The same procedure applies to NLDAS-2,
HRRR, and the PRISM daily extrema before they are compared on the NWM grid.

This is a deterministic downscaling correction, not a claim that the free-atmospheric lapse
rate is correct during every inversion or stable boundary layer. Store the rate in configuration
so later evaluation can support seasonal, spatially varying, or dynamically estimated rates.

### Remapping rules

- Elevation-normalized air temperature: bilinear interpolation directly to the NWM grid.
- Continuous diagnostic fields: bilinear where meaningful.
- Masks, source identifiers, and categorical QC: nearest neighbor.
- Target elevation: sample or area-average GMTED2010 consistently and cache the result.
- Unmapped active-domain cells: remain missing until baseline selection; never turn missing
  source values into zero temperature.

Precomputed ESMF/CDO weights are maintained separately for every distinct native grid and
revision. Weight metadata must include source-grid fingerprints, target-grid fingerprint,
method, mask handling, and tool version.

## PRISM daily Tmin/Tmax reconciliation

### Revision classes

PRISM corrections use the same revision vocabulary as precipitation:

- `early`: current-month data expected to change.
- `provisional`: approximately one to six months old.
- `stable`: more than approximately six months old.
- `none`: PRISM was unavailable and no daily correction was applied.

Near-real-time output may use early or provisional PRISM but remains mutable. The rolling
revision window is regenerated when PRISM changes. Stable retrospective output uses stable
PRISM and is republished by complete PRISM day.

### Constraint procedure

Let `T(i,h)` be the elevation-adjusted preliminary hourly temperature at NWM cell `i`. Let
`Tmin_p(i)` and `Tmax_p(i)` be lapse-normalized, bilinearly remapped, and target-elevation-
adjusted PRISM constraints for the same 1200-1200 UTC day. Compute:

```text
Tmin_b = min over h T(i,h)
Tmax_b = max over h T(i,h)
M_b    = (Tmax_b + Tmin_b) / 2
R_b    = Tmax_b - Tmin_b

M_p    = (Tmax_p + Tmin_p) / 2
R_p    = Tmax_p - Tmin_p
```

For a valid baseline range, correct every hourly value with an affine transformation:

```text
T_corrected(i,h) = M_p + (R_p / R_b) * (T(i,h) - M_b)
```

This preserves the timing and relative shape of the baseline diurnal cycle while making its
minimum and maximum equal the PRISM constraints. It does not force the 24-hour arithmetic mean
to equal PRISM `tmean`.

Unlike precipitation reconciliation, the temperature correction is pointwise after all fields
have been remapped to the NWM grid. PRISM contributes a broad-scale daily constraint; it does
not create independently observed 1-km hourly structure.

### Degenerate, missing, and implausible cases

| Condition | Action |
|---|---|
| Valid baseline and PRISM extrema | Apply midpoint shift and range scaling. |
| PRISM `tmin > tmax` or either is nonfinite | Reject the constraint and retain the baseline. |
| PRISM missing | Retain the elevation-adjusted baseline. |
| Baseline incomplete for the PRISM day | Do not claim exact extrema reconciliation; retain or publish according to the configured completeness policy and flag it. |
| Baseline range below a configured threshold | Apply only the PRISM midpoint shift, unless a separately validated neighboring/climatological amplitude method is enabled. |
| Range ratio outside configured bounds | Cap or reject amplitude scaling, preserve the midpoint correction, and flag the cell. |

Thresholds and caps are calibration parameters and are deliberately not fixed in this design
document. The implementation must report how often each safeguard is activated. Corrected
hourly values must be finite and physically plausible, and an optional small numerical cleanup
may enforce exact extrema after floating-point calculations.

## Output and provenance

The production output contains at least:

- `T2D(time, y, x)` in kelvin, or the exact temperature variable name required by the target
  NWM forcing convention.
- `temperature_source_id(time, y, x)`.
- `temperature_qc_flags(time, y, x)` as a bit mask.
- `temperature_elevation_adjustment(time, y, x)` in kelvin.
- Daily baseline and PRISM minimum, maximum, midpoint, and range diagnostics.
- Daily midpoint shift and range-scaling factor.
- `prism_revision(day)` and PRISM release metadata.
- Complete source file, source-terrain, DEM, and remapping-weight provenance.

Suggested stable source identifiers are:

```text
0 missing
1 NLDAS-2
2 HRRR analysis
```

Suggested QC flags include missing baseline, HRRR provisional source, source-terrain
substitution, missing PRISM, incomplete PRISM day, invalid PRISM extrema, degenerate baseline
range, capped range scaling, and failed post-correction extrema validation.

## Operational modes

### Near-real-time

- Produce recent hours from HRRR analysis as they become available.
- Apply early/provisional PRISM after a complete PRISM day is published.
- Replace HRRR baseline days with NLDAS-2 when NLDAS-2 becomes available.
- Reprocess the configured rolling PRISM revision window.
- Publish complete day revisions atomically and label output as mutable.

### Stable retrospective

- Use NLDAS-2 as the hourly baseline except for documented gaps.
- Apply stable PRISM Tmin/Tmax constraints.
- Regenerate complete PRISM days atomically.
- Mark output stable only after all validation checks pass.

Outputs should be versioned rather than overwritten without trace. Atomic temporary-file
replacement prevents readers from encountering a partially written forcing day.

## Additional acquisition plan

The current download implementation already requests the required meteorological fields:

- NLDAS-2 File A contains the hourly temperature baseline.
- Each HRRR subset contains the `TMP:2 m above ground:anl` record.
- PRISM downloads are configured for `tmin`, `tmax`, and `tmean` as well as precipitation.

Before production implementation and testing:

1. Extend NLDAS-2, HRRR, and PRISM downloads across a common calibration period containing at
   least several complete months and seasons. Include overlap on both sides of the expected
   NLDAS-2 latency boundary.
2. Ensure every PRISM date has all three temperature products and preserve their release
   metadata. Processing may proceed with Tmin/Tmax when Tmean is absent, but the missing
   validation field must be reported.
3. Acquire authoritative native NLDAS-2 and HRRR terrain grids once per grid definition, or
   derive and fingerprint documented GMTED2010 aggregates as the initial fallback.
4. Inventory completeness before submitting bulk reprocessing. A nominal complete UTC day has
   24 NLDAS-2 files and 24 HRRR temperature analyses; a complete PRISM day has Tmin and Tmax,
   with Tmean expected for validation.
5. Retain enough temporal overlap to compare NLDAS-2 and HRRR directly and quantify boundary
   bias before selecting any blend or bias-correction rule.

Date ranges and calibration periods belong in operational configuration or run manifests, not
in this scientific design. Existing commands can extend meteorological coverage without new
hourly download code:

```bash
hydro-ops submit nldas2 --start YYYY-MM-DD --end YYYY-MM-DD
hydro-ops submit hrrr   --start YYYY-MM-DD --end YYYY-MM-DD
hydro-ops submit prism  --start YYYY-MM-DD --end YYYY-MM-DD
```

## Validation and acceptance tests

### Unit tests

- Celsius-to-kelvin conversion and metadata.
- PRISM day boundaries, including leap days and samples exactly at 1200 UTC.
- Source selection for NLDAS-2 availability, HRRR fallback, and missing inputs.
- Fixed-lapse-rate sign and magnitude for higher and lower target elevations.
- Affine correction of midpoint and range.
- Degenerate range, missing PRISM, invalid extrema, and range-cap branches.
- Provenance and QC bit encoding.

### Remapping tests

- Constant elevation-normalized fields remain constant.
- A synthetic constant-lapse-rate atmosphere reproduces target-grid temperature after
  remapping over variable terrain.
- Source and target masks do not turn missing values into zeros.
- Native-grid fingerprints reject stale weights.
- Repeated remapping is deterministic.

### Integration tests

- A complete 1200-1200 UTC PRISM day using NLDAS-2.
- A recent complete day using HRRR.
- A transition from HRRR to newly available NLDAS-2.
- A PRISM revision followed by complete-day regeneration.
- Complex terrain with large source-to-target elevation differences.
- Missing hours and missing PRISM cells.
- A near-isothermal baseline day that activates the range safeguard.

### Production acceptance criteria

- Exactly 24 hourly samples participate in every day labeled completely reconciled.
- Corrected daily Tmin and Tmax match valid target-grid PRISM constraints within a documented
  numerical tolerance when no safeguard prevents exact matching.
- The corrected hourly curve retains the baseline times of minimum and maximum unless ties make
  them ambiguous.
- No nonfinite active-domain values or physically implausible temperatures are published.
- Every active output cell has a source identifier and QC state.
- Every fallback, missing input, terrain substitution, and correction safeguard is counted in
  the job summary.
- Stable results are reproducible from their run manifest and recorded input revisions.

## Implementation phases

1. Inventory and extend overlapping NLDAS-2, HRRR, and PRISM temperature coverage.
2. Acquire or derive source-grid terrain and target-grid elevations; fingerprint all static
   inputs.
3. Normalize temperature variables, units, time coordinates, masks, and metadata.
4. Generate and validate bilinear weights for NLDAS-2, HRRR, and PRISM to the NWM grid.
5. Implement lapse normalization, direct remapping, target-elevation restoration, hourly source
   selection, and provenance.
6. Implement PRISM-day diagnostics without modifying the hourly baseline.
7. Implement Tmin/Tmax affine correction and all safeguard/QC branches.
8. Add rolling near-real-time replacement and stable retrospective publication.
9. Evaluate lapse rate, range caps, and the NLDAS-2/HRRR boundary against independent stations.

## Current implementation status

As of 2026-08-22:

- The authoritative NASA NLDAS mean-elevation grid, one native HRRR terrain/grid definition,
  and the GMTED2010-derived NWM target elevation are available below `data/static`.
- Normalized NLDAS-2, HRRR, and PRISM readers validate exact fields and units and attach stable
  source-grid fingerprints.
- NLDAS-2, HRRR, and PRISM bilinear weights to the NWM grid have been generated with manifests.
- Lapse-rate adjustment, hydrostatic pressure, humidity/RH conversion, and guarded PRISM
  Tmin/Tmax affine correction are implemented as vectorized, unit-tested primitives.
- NLDAS-2 and HRRR temperature fields have been remapped successfully to the complete NWM grid
  in smoke tests.
- Whole-hour NLDAS-2 preference/HRRR fallback, runtime weight and terrain validation, and
  seven-field LDASIN assembly are implemented.
- GMTED2010 has been sampled onto the PRISM grid. PRISM Tmin/Tmax are lapse-normalized,
  bilinearly remapped, restored to NWM elevation, and applied to complete 24-hour curves with
  midpoint-only and scale-clipping safeguards. Real-grid constraint preparation and synthetic
  exact-extrema reconciliation tests pass.

Rolling revision orchestration, archive-wide application, transition calibration, and
independent evaluation remain incomplete. The ongoing WY2023-WY2025 backfill must pass the
range-completeness audit before calibration claims are made.

## References

- Cosgrove, B. A., et al. (2003), *Real-time and retrospective forcing in the North
  American Land Data Assimilation System (NLDAS) project*,
  <https://doi.org/10.1029/2002JD003118>.
- NASA GES DISC NLDAS-2 forcing documentation,
  <https://ldas.gsfc.nasa.gov/nldas/v2/forcing>.
- NOAA HRRR archive documentation,
  <https://registry.opendata.aws/noaa-hrrr-pds/>.
- PRISM dataset documentation,
  <https://www.prism.oregonstate.edu/documents/PRISM_datasets.pdf>.
- PRISM time-series revision policy, <https://prism.oregonstate.edu/data/>.
