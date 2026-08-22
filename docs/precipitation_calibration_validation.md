# Precipitation calibration and withheld validation

## Purpose

This protocol determines whether MRMS quality thresholds or regional Stage-IV overrides improve
the hourly precipitation compositor. It also evaluates PRISM-reconciled daily fields. The
protocol separates parameter fitting from final assessment so withheld results remain an honest
estimate of production performance.

The implemented code is evaluation machinery, not evidence that a particular rule is skillful.
No threshold becomes a production default until the independent observations have been audited,
the archive is complete for the selected period, and the promotion gates below pass.

## Evaluation units and leakage control

Use whole storms as the preferred grouping unit. A 12Z-to-12Z PRISM day may be used when a storm
catalog is not yet available. Every hour, station, grid cell, and basin total associated with a
group must remain entirely in either calibration or withheld validation.

`deterministic_group_split` assigns groups by a salted SHA-256 hash. The assignment is stable as
new samples are appended and does not depend on input row order. Record the salt and calibration
fraction in every result. Do not repeatedly change the salt after inspecting withheld scores.

Spatial independence is also required. Where gauges contributed to MRMS, Stage-IV, PRISM, or
NLDAS analyses, identify a genuinely withheld gauge network or use leave-network/leave-region-out
experiments. Basin validation should preferentially use basins whose reference observations are
not simply the same gridded product aggregated to a polygon.

## Calibration sample contract

`bin/calibrate_precipitation_overrides.py` accepts an uncompressed or compressed NumPy NPZ file.
It requires equally shaped arrays:

| Array | Meaning |
|---|---|
| `reference` | Independent hourly precipitation depth, in mm or `kg m-2`. |
| `quality` | Remapped MRMS radar accumulation quality index in `[0,1]`. |
| `strata` | Region-season class used to fit separate rules. |
| `group` | Whole storm or PRISM-day identifier used for leakage-free splitting. |
| `mrms_pass2` | MRMS Pass 2 hourly depth at the observation support. |
| `stage4_archive` | Stable Stage-IV hourly depth at the same support. |

All candidate and reference values must represent the same `(T-1h,T]` interval and observation
support. Point gauges require an explicitly documented point-to-grid comparison method. Basin
totals require area-consistent aggregation. Missing values are `NaN`; missing observations must
never be encoded as zero.

Recommended strata initially combine broad hydroclimatic region and season. Avoid very fine
strata until sample counts demonstrate adequate storms, wet hours, extremes, and withheld
coverage. Elevation, radar-beam blockage, gauge density, and precipitation phase can be added as
diagnostic strata before they are considered as production decision variables.

## Baseline scorecard

Before fitting overrides, score MRMS Pass 1, MRMS Pass 2, Stage-IV archive, NLDAS-2, HRRR, and
the current compositor against exactly the same samples. Report:

- count, bias, MAE, RMSE, and correlation;
- probability of detection, false-alarm ratio, and critical success index at documented wet
  thresholds;
- daily and storm-total bias and error;
- upper-tail and extreme-event performance;
- basin-total and spatial-pattern performance where appropriate;
- results by season, region, elevation, intensity, quality class, and source selection; and
- source-transition frequency and discontinuity.

Keep an immutable machine-readable baseline report with source file identities, software
revision, split salt, units, thresholds, and observation provenance.

## Regional Stage-IV rule fitting

Run the candidate sweep with:

```bash
python bin/calibrate_precipitation_overrides.py \
  --samples work/evaluation/precipitation_samples.npz \
  --quality-thresholds 0.2,0.4,0.5,0.6,0.8 \
  --disagreement-thresholds 1,2,5,10,25 \
  --calibration-fraction 0.7 \
  --output outputs/evaluation/stage4_override_calibration.json
```

Within each stratum, the current selector minimizes calibration RMSE and uses critical success
index as a tie-break. The chosen rule is then evaluated once on withheld groups. The report is
not a runtime configuration file: review and promotion are separate deliberate steps.

Extend the candidate rule only when diagnostics support doing so. Likely predictors include
MRMS quality, absolute or relative MRMS–Stage-IV disagreement, radar coverage/beam blockage,
terrain, event intensity, phase, and revision status. Complexity must be justified by stable
withheld improvement rather than calibration improvement alone.

## PRISM reconciliation evaluation

Evaluate preliminary and reconciled fields separately. A stable retrospective PRISM run must:

- contain exactly 24 contiguous hourly intervals for each 12Z-ending day;
- contain no negative precipitation;
- reproduce finite PRISM daily totals after NWM-to-PRISM reaggregation within the configured
  tolerance, except where a documented correction cap makes the constraint infeasible;
- report every missing constraint, dry/wet synthesis, cap, residual, and non-convergence case;
- preserve credible hourly timing and storm evolution; and
- improve daily and basin totals without degrading withheld hourly or extreme-event skill
  beyond an approved tolerance.

Early and provisional PRISM results remain mutable. Store their revision class and source
release metadata, and never label them retrospective final output.

## Promotion gates

Freeze a versioned production rule only when all of these are true:

1. Input inventories and time/support alignment pass audit.
2. Calibration and withheld groups are disjoint, and observation independence is documented.
3. Every production stratum has adequate calibration and withheld event counts.
4. Improvement is consistent across relevant seasons, regions, intensities, and basins—not
   driven by a few large events.
5. Extreme-event, false-alarm, and transition behavior remain acceptable.
6. Sensitivity tests show nearby thresholds give similar conclusions.
7. The exact rule, split, metrics, source revisions, code commit, and reviewer decision are
   recorded in a versioned calibration report.

After promotion, production reads a frozen configuration rather than rerunning calibration.
New data can trigger a new calibration version, but it must not silently modify an existing
retrospective forcing version.

## Current status

As of 2026-08-22, the split, continuous and categorical metrics, regional rule sweep, sparse
conservative reconciliation, and diagnostics are implemented and unit-tested. The reverse
NWM-to-PRISM weights, independent gauge/basin sample archive, baseline scorecards, fitted rules,
and archive-wide reconciliation results are not yet complete. Consequently, the current MRMS
quality threshold remains provisional and no regional Stage-IV override is a production default.
