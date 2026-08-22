# NWM 1-km precipitation production workflow

## Purpose

This workflow produces hourly precipitation forcing on the National Water Model (NWM)
CONUS 1-km grid. It combines MRMS, Stage-IV, NLDAS-2, and HRRR according to availability,
quality, latency, location, and effective resolution. PRISM is used as an independent daily
amount constraint rather than as an hourly source.

The workflow has two objectives that must be satisfied together:

1. Preserve the best available hourly timing and fine-scale storm structure.
2. Reconcile 1200–1200 UTC daily totals with PRISM when an appropriate PRISM revision is
   available.

Every output must retain enough provenance to explain which source and corrections were used
at every target cell and hour.

## Why there is no universal source ranking

The products are neither independent nor uniformly superior:

- **MRMS Multi-Sensor QPE** is automated, approximately 1 km, and combines radar, gauges,
  model QPF, and climatology. Pass 2 normally contains more gauge reports than Pass 1. Radar
  quality varies with beam blockage, beam height, range, the melting layer, and terrain.
- **Stage-IV** is approximately 4–5 km and mosaics River Forecast Center multisensor
  analyses. It includes substantial manual quality control and may use gauge- and
  climatology-based methods where western radar coverage is poor. It can therefore be
  preferable to MRMS in some regions and events despite its coarser grid.
- **NLDAS-2** precipitation includes observation-based information and is valuable as a
  spatially complete fallback, but its approximately 0.125-degree grid is much coarser.
- **HRRR** provides spatially detailed, timely coverage but its hourly accumulation is a model
  forecast. It is the final fallback when observation-informed products are unavailable.
- **PRISM AN daily precipitation** uses station observations and physiographically informed
  interpolation. It is well suited to constrain daily amounts, especially in complex terrain,
  but it does not supply hourly timing. Recent grids are revised repeatedly and become stable
  only after approximately six months.

Consequently, source selection must be conditional. A fixed national overwrite order would
hide known variations in quality and produce artificial source boundaries.

## Time and unit conventions

The internal precipitation quantity is an hourly accumulated depth in `kg m-2`, numerically
equivalent to millimetres of liquid water. Accumulations use half-open intervals `(T-1h, T]`
and are labeled by ending time `T` in UTC.

PRISM uses a day-ending convention. A PRISM grid labeled date `D` represents the period:

```text
(D - 1 day) 12:00 UTC through D 12:00 UTC
```

Only after spatial blending and PRISM reconciliation is hourly depth converted to the NWM
forcing variable:

```text
RAINRATE = hourly_depth / 3600
```

where `RAINRATE` has units `kg m-2 s-1`.

## Source preparation

Each source adapter must produce the same logical fields before compositing:

- Hour-ending time bounds.
- Hourly precipitation depth in millimetres.
- Valid/missing mask.
- Source-specific quality fields.
- Product version, pass, publication time, and source filename.
- Native-grid cell centers and bounds.

Source-specific rules include:

### MRMS

- Keep Pass 1 and Pass 2 as separate candidates.
- Prefer Pass 2 when it is available and passes quality checks.
- Retain Pass 1 so near-real-time output can be produced before Pass 2 arrives.
- Use `RadarAccumulationQualityIndex_01H` in the confidence assessment.
- Add `GaugeInflIndex_01H_Pass1` in a later enhancement if testing shows additional value.
- Treat MRMS missing and no-coverage codes as missing, not zero precipitation.

### Stage-IV

- Use only CONUS one-hour accumulations for hourly compositing.
- Never mix 1-, 6-, and 24-hour records as interchangeable observations.
- Treat the stable archive and mutable realtime feed as distinct revisions.
- Record the contributing accumulation file and stream.

### NLDAS-2

- Extract the observation-based hourly precipitation field and verify its time bounds.
- Use it as a spatially complete fallback, not as a fine-scale pattern source where higher
  quality observations exist.

### HRRR

- Use the one-hour `APCP` accumulation from the preceding cycle's `f01` file, valid at the
  target hour.
- Do not use the zero-duration `f00 APCP` record.
- Mark HRRR precipitation explicitly as model-derived.

## Spatial remapping

All hourly candidates are conservatively remapped from their native grids directly to the NWM
1-km grid. There is no intermediate 4-km forcing grid. An intermediate grid would discard MRMS
and HRRR spatial structure before the final NWM mapping.

Precomputed ESMF/CDO weights are maintained separately for every distinct native grid and
revision. Weight metadata must include source-grid fingerprints, target-grid fingerprint,
method, mask handling, and tool version.

Remapping rules:

- Hourly precipitation depth: first-order conservative.
- Continuous confidence indices: bilinear where meaningful.
- Categorical masks and source flags: nearest neighbor.
- Unmapped target cells: remain missing until filled by the source-selection stage.

Tests must demonstrate conservation using cell areas, not merely equality of unweighted grid
sums.

## Conditional source selection

For every NWM cell and hour, each available source receives eligibility flags and a confidence
assessment. The initial deterministic rules are:

1. Apply any configured Stage-IV override where Stage-IV is valid and MRMS quality or an
   independently established regional/event rule warrants the override.
2. Otherwise use MRMS Pass 2 where available and acceptable.
3. Otherwise use acceptable MRMS Pass 1.
4. Otherwise use valid Stage-IV.
5. Use NLDAS-2 where neither acceptable MRMS nor Stage-IV is available.
6. Use HRRR where no observation-informed source is available.

Stage-IV may outrank otherwise valid MRMS in configurable regions or conditions, including
poor radar quality and terrain blockage. These exceptions must be driven by documented rules,
not manual changes to individual output files.

The confidence assessment may consider:

- MRMS accumulation quality.
- Missing data and radar coverage.
- MRMS pass and latency.
- Stage-IV stream and revision status.
- Effective source resolution relative to the target.
- Region, season, terrain, and independently established performance assessments.
- Temporal completeness of the 24-hour PRISM day.

Numeric thresholds and regional masks are configuration parameters to be calibrated through
evaluation. They are deliberately not fixed in this design document.

The first implementation should use deterministic selection rather than weighted averaging.
This is easier to audit and avoids blurring precipitation features. If hard source transitions
produce artifacts, a later version may feather confidence-weighted boundaries while retaining
the dominant source identifier.

## PRISM daily reconciliation

### Revision classes

PRISM corrections are labeled by revision state:

- `early`: current-month data expected to change.
- `provisional`: approximately one to six months old.
- `stable`: more than approximately six months old.
- `none`: PRISM was unavailable and no daily correction was applied.

Near-real-time forcing may use early or provisional PRISM, but it remains mutable. A scheduled
revision workflow reprocesses the rolling PRISM window. Stable retrospective output is created
only after stable PRISM becomes available.

### Constraint procedure

Let `x(i,h)` be preliminary hourly precipitation at NWM cell `i`, and let `W(j,i)` be the
conservative overlap operator from NWM cells to PRISM cell `j`. For each PRISM day:

1. Form preliminary NWM daily depth:

   ```text
   X(i) = sum over the 24 hours x(i,h)
   ```

2. Aggregate it conservatively to the PRISM grid:

   ```text
   Q(j) = W X
   ```

3. Compare `Q(j)` with PRISM daily depth `P(j)`.
4. Solve for a nonnegative corrected NWM daily field `Y(i)` that remains as close as possible
   to `X(i)` while satisfying, within numerical tolerance:

   ```text
   W Y = P
   ```

   A multiplicative iterative proportional fitting or equivalent constrained method should be
   used so MRMS-scale relative spatial structure is retained wherever possible.

5. Preserve the preliminary hourly fractions at each NWM cell:

   ```text
   f(i,h) = x(i,h) / X(i)
   y(i,h) = Y(i) f(i,h)
   ```

6. Reaggregate `y(i,h)` to the PRISM grid and verify the 24-hour constraint.

This approach is preferable to interpolating a PRISM/base ratio back to 1 km because simple
ratio interpolation does not guarantee that reaggregated NWM totals reproduce PRISM.

### Zero and missing cases

The correction must define these cases explicitly:

| Preliminary daily depth | PRISM depth | Action |
|---|---|---|
| zero | zero | Keep all hours zero. |
| positive | zero | Set the corrected daily and hourly values to zero. |
| positive | positive | Apply the constrained multiplicative correction. |
| zero | positive | Obtain temporal fractions from the best available broader spatial evidence. |
| any | missing | Do not apply a PRISM constraint; retain preliminary forcing. |

For a wet PRISM cell with no preliminary precipitation, temporal fractions are sought in this
order:

1. Any valid lower-priority source within the same PRISM cell.
2. A quality-controlled precipitation-weighted profile from nearby wet cells in the same event.
3. A documented last-resort distribution, flagged as synthetic and assigned the lowest
   confidence.

The last-resort rule must never be applied silently. Ratio caps or small-denominator thresholds
may be used for numerical stability, but all affected cells must be flagged and the resulting
failure to meet the PRISM constraint quantified.

## Output and provenance

The production output contains at least:

- `RAINRATE(time, y, x)` in `kg m-2 s-1`.
- `precip_source_id(time, y, x)`.
- `precip_confidence(time, y, x)` or a documented quality class.
- `precip_qc_flags(time, y, x)` as a bit mask.
- `prism_correction_factor(day, y, x)` or an equivalent diagnostic.
- `prism_revision(day)` and PRISM release metadata.
- Hourly time bounds.
- Complete source file and remapping-weight provenance.

Suggested source identifiers are stable integers with a lookup attribute:

```text
0 missing
1 MRMS Pass 2
2 MRMS Pass 1
3 Stage-IV archive
4 Stage-IV realtime
5 NLDAS-2
6 HRRR
7 synthetic temporal fallback
```

If multiple native cells contribute to one NWM cell, `precip_source_id` records the selected
product, not individual native-cell lineage. Detailed file lineage remains in global metadata or
a sidecar manifest.

## Operational modes

### Near-real-time

- Produce an initial hour when MRMS Pass 1 or another eligible source arrives.
- Revise it when MRMS Pass 2 or better Stage-IV data becomes available.
- Apply early/provisional PRISM when published.
- Reprocess the configured rolling revision window.
- Clearly label output as mutable.

### Stable retrospective

- Use final available MRMS and Stage-IV revisions.
- Apply stable PRISM.
- Regenerate complete PRISM days atomically.
- Mark output stable only after validation passes.

Outputs should be versioned rather than overwritten without trace. Publication should use an
atomic temporary-file replacement so readers never encounter partially written forcing.

## Validation and acceptance tests

### Unit tests

- Unit and accumulation conversions.
- Hour-ending and PRISM day-ending boundaries, including leap days.
- Source eligibility and precedence for every branch.
- MRMS Pass 2 preference and Pass 1 fallback.
- Quality-threshold and regional Stage-IV exceptions.
- Zero/zero, wet/zero, zero/wet, and missing PRISM cases.
- Provenance and QC bit encoding.

### Remapping tests

- Constant fields remain constant.
- A single-cell impulse conserves area-integrated water.
- Source and target masks do not turn missing values into zeros.
- Native-grid fingerprints reject stale weights.
- Repeated remapping is deterministic.

### Integration tests

- A complete 1200–1200 UTC PRISM day.
- Missing MRMS Pass 2 with valid Pass 1.
- Low-quality MRMS with Stage-IV replacement.
- Radar gaps in complex western terrain.
- A PRISM-wet/base-dry case.
- Rerun after a PRISM revision.

### Production acceptance criteria

- No negative precipitation or nonfinite active-domain values.
- Exactly 24 hourly intervals for every complete PRISM day.
- Reaggregated stable daily precipitation matches PRISM within a documented area-weighted
  tolerance.
- Area-integrated precipitation is conserved by each conservative remap within tolerance.
- Every active output cell has a source identifier and QC state.
- Every correction and fallback count is reported in the job summary.

## Implementation phases

1. Normalize source precipitation, time bounds, masks, and metadata.
2. Generate and validate conservative weights for each source grid to the NWM grid.
3. Implement deterministic hourly source selection and provenance output.
4. Implement PRISM-day aggregation and diagnostics without correction.
5. Implement constrained PRISM reconciliation and zero-case handling.
6. Add rolling near-real-time revisions and stable retrospective publication.
7. Evaluate thresholds and regional exceptions against independent gauges and basin totals.

## Current implementation status

As of 2026-08-22, the downloaders and recurring refresh workflows are implemented for all five
precipitation sources. Direct conservative NLDAS-2, HRRR, and PRISM-to-NWM weights have been
generated with reproducibility manifests. HRRR weights use the preceding-cycle `f01` APCP field
already retained in each hourly subset, with native GRIB geometry used for conservative cell
corners.

The following components are now implemented and tested:

- Native adapters for MRMS Pass 1, Pass 2, quality index, Stage-IV one-hour accumulation,
  NLDAS-2, and HRRR with explicit `(T-1h,T]` bounds and `kg m-2` depth.
- Correct masking of MRMS negative no-coverage codes and Stage-IV missing cells.
- Fingerprinted conservative MRMS and Stage-IV weights plus bilinear MRMS-quality weights.
  Native GRIB geometry supplies corners omitted by wgrib2 NetCDF conversion.
- Deterministic target-cell selection, configurable MRMS quality threshold, Stage-IV override,
  confidence, stable source IDs, and QC flags.
- Atomic `RAINRATE` publication and complete eight-variable LDASIN assembly.
- Resumable range and rolling SLURM-array orchestration with per-hour JSON manifests.
- Generic and stratified validation metrics plus MRMS threshold-sweep scaffolding.
- Deterministic whole-event/day calibration-versus-withheld assignment, continuous and
  categorical precipitation scores, and a regional Stage-IV rule sweep that never selects
  parameters from withheld samples.
- Sparse CDO/SCRIP conservative-operator ingestion and bounded iterative PRISM reconciliation,
  including explicit missing, dry-target, dry-baseline/wet-target, synthetic-timing, ratio-cap,
  residual, and convergence diagnostics.
- Atomic 24-hour LDASIN reconciliation into a separate revision-labeled output directory and
  a daily diagnostic NetCDF.

A real 2026-07-24 10 UTC integration hour combined all five candidate families and filled every
active NWM cell: 8,318,990 cells used MRMS Pass 2, 1,115,915 Stage-IV, 1,421,757 NLDAS-2, and
628 HRRR. No negative precipitation was published.

The reconciliation and calibration machinery is implemented and unit-tested, but production
promotion remains intentionally incomplete. Reverse NWM-to-PRISM conservative weights must be
generated and validated, the archive must finish, and an independent gauge/basin sample table
must be assembled. No regional override or quality threshold is frozen until its rules have
been fitted only on calibration groups and evaluated once on withheld storms/days. A real
archive-wide PRISM reconciliation and rolling revision run also remains to be performed.

The operational data contract, leakage controls, scorecard, calibration command, review gates,
and parameter-promotion procedure are specified in
[Precipitation calibration and validation](precipitation_calibration_validation.md).

## References

- Cosgrove, B. A., et al. (2003), *Real-time and retrospective forcing in the North
  American Land Data Assimilation System (NLDAS) project*,
  <https://doi.org/10.1029/2002JD003118>.
- NOAA MRMS operational GRIB2 tables,
  <https://www.nssl.noaa.gov/projects/mrms/operational/tables.php>.
- NOAA MRMS v12 QPE update summary,
  <https://inside.nssl.noaa.gov/mrms/past-code-updates/>.
- NOAA/NWS National Water Prediction Service product guide (Stage-IV description),
  <https://www.weather.gov/media/owp/operations/nwps_user_guide.pdf>.
- PRISM dataset documentation,
  <https://www.prism.oregonstate.edu/documents/PRISM_datasets.pdf>.
- PRISM time-series revision policy, <https://prism.oregonstate.edu/data/>.
