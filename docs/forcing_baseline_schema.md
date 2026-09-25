# Baseline schema compatibility

Daily collections assembled from all eight NWM fields now carry
`baseline_schema_version="1"`. This is independent of chunk dimensions,
compression, source choice, PRISM adjustment, and calendar-day layout.
Hourly staging files may still have source-specific diagnostics; the common
archive writer normalizes them when creating a daily baseline or PRISM window.

The eight physical fields remain RAINRATE, T2D, Q2D, PSFC, SWDOWN, LWDOWN,
U2D, V2D. Auxiliary fields always include:

| Field | Type | Dimensions | Legacy absence |
|---|---|---|---|
| gfs_fallback_qc | uint8 | time,y,x | missing/unknown |
| gfs_forecast_reference_time | float64 | time | missing/unknown |
| gfs_forecast_lead_hours | int16 | time | missing/unknown |
| native_donor_qc | uint8 | time,y,x | missing/unknown |
| native_donor_distance_km | float32 | time,y,x | missing/unknown |
| precip_timing_source_id | uint8 | same as precip_source_id | zero: no separate timing provenance |

Existing diagnostic values and missing masks are preserved. In particular,
missing legacy QC is **not** converted to zero (which would assert that no
repair occurred). The established precipitation timing convention is retained:
zero does not distinguish unavailable provenance from no separate timing donor.
For newly evaluated GFS hours, zero QC means no action; an NLDAS-only hour has
zero GFS lead and a missing reference time. Missing lead plus missing reference
time indicates unknown legacy information, not a freshly evaluated NLDAS hour.

Both decoded and compressed-chunk assembly use this compatibility layer.
Only auxiliary variables requiring synthesis use decoded assembly in the chunk
writer; compatible physical chunks continue to be copied losslessly. Unknown
variables are not silently dropped. Variable dtype/dimension mismatches, physical
units/packing/fill-value mismatches, and incompatible grid dimensions still fail.
Existing static-coordinate integrity checks remain in effect.

No historical file is rewritten merely to upgrade its schema. Legacy baselines
are normalized virtually during assembly, without modifying their receipts,
identities, or source files. This avoids a retro-archive rewrite and avoids
invalidating unchanged baseline receipts solely because a schema version changed.
Final model files remain model-consumable LDASIN collections; downstream readers
must not interpret auxiliary variables as meteorological fields.

## Validation and rollout

Regression tests cover both writers, legacy-first and modern-first order,
missing diagnostic masks, physical-value preservation, unchanged source files,
and rejection of incompatible physical metadata. The real September 14–15,
2026 baseline boundary passes the read-only schema preflight.

`sbatch slurm/test_nrt_baseline_schema.sh` runs full-size PRISM reconciliation
for September 15 into `forcing/work/nrt-schema-v1-JOBID/output`, using real
September 14–16 baselines. It does not overwrite published NRT forcing or remove
baseline files. Inspect its completed publication audit and timing before
repairing the published day or claiming end-to-end acceptance.

Submitted September 23, 2026 as job **4626557**. Its output root is
`forcing/work/nrt-schema-v1-4626557/output`; log:
`forcing/logs/nrt-schema-v1-4626557.out`. Submission-time validation: 58 focused
tests passed and lint passed. This first job failed after 17m25s: both PRISM
windows passed their acceptance thresholds, but calendar-day assembly rejected
`gfs_forecast_lead_hours/units` (present on synthesized legacy diagnostics and
absent on existing modern diagnostics). No final file was published.

The follow-up fix normalizes documented diagnostic attributes in a shared
variable view used by both writers. Missing lead units become `hours`; missing
distance units become `km`; QC flag definitions and reference-time calendar are
normalized as well. Contradictory attributes still fail. An existing reference
time without epoch units is rejected rather than guessed. Existing data and
missing-value masks are not changed. The raw-chunk units check relies on this
prior validation only for canonical diagnostics; packing/fill checks remain.
The GFS producer now explicitly writes lead-time units too.

The regression suite now includes second-stage calendar assembly with missing
units in an already-created window, both input orders, both writers, source
immutability, and rejection of conflicting units. All 62 focused tests passed;
real September 14–16 baseline metadata passed preflight.

Rerun submitted as **4626634**, using the same isolated test script. Log:
`forcing/logs/nrt-schema-v1-4626634.out`; output root:
`forcing/work/nrt-schema-v1-4626634/output`. Completed successfully in 23m22s:
24 records for September 15, 00–23 UTC; both PRISM windows accepted; final
domain audit reports zero missing required active cells across all eight fields.
Older chunks required the decoded fallback writer. Published NRT files and
baseline inputs remained untouched by this isolated test.

Operational follow-up **4627429** uses `slurm/test_nrt_schema_operational.sh` to
repair September 15 through `run_cycle` with production paths and its normal
lock, then repeat without external refresh. It requires unchanged status and
unchanged file identities on the second cycle. Reports are saved in
`forcing/status/layout-migration/operational-gate-4627429/`. This failed attempt
blocked migration until its corrected rerun passed; the test script itself
cannot migrate data or release NWM.

### Operational input-selection correction

Job 4627429 failed after 21m36s at the final active-cell audit, before publication
or the no-op cycle. The schema checks succeeded. The actual window manifest
showed leftover September 14 hourly files being chosen ahead of its repaired
daily archive. Each of those hourly files had 57,934 missing active T2D cells;
the checked corresponding daily records had none. Missing values propagated
through the 24-hour temperature adjustment into 11,461 active cells during
September 15 00–11 UTC, also affecting coupled humidity and longwave. Every
new hole lay within the stale hourly missing mask. The isolated test's generic
post-PRISM repair had concealed this input-selection error.

The NRT controller now passes `--baseline-archives` with the exact daily files
returned by its baseline acceptance/production step. The PRISM worker resolves
timestamps only within that list, rejects ambiguous or absent records, and never
falls back to leftover hourly files. Legacy standalone discovery is unchanged.
`explicit_accepted_daily_archives_v1` is included in final-input and window-cache
fingerprints, invalidating old selection results without rebuilding valid
baselines. Baseline file identities are checked again before storing newly
computed windows and before final publication. No extra nearest-neighbor filling
or historical-file deletion was introduced.

Validation: 43 focused tests passed, including mixed leftover hourly/daily
inputs, missing/duplicate selected records, date-boundary lookup, and cache
version invalidation. The corrected production repair/no-op gate subsequently
passed, clearing the operational prerequisite for migration.

Rerun submitted as **4627624**. Log:
`forcing/logs/nrt-schema-operational-4627624.out`; acceptance reports:
`forcing/status/layout-migration/operational-gate-4627624/`. It uses production
paths and automatically runs the unchanged-input check after successful repair.
It completed successfully in 18m07s: September 15 was published with accepted
PRISM constraints and no missing required active cells. The repeat took about
8 seconds, reported unchanged, and preserved all three baseline file identities
and the published NRT identity.

Post-layout NRT gate 4627946 also passed (18 seconds total, both cycles unchanged).
The CONUS model test and final migration gate passed; NWM 1986 was released.
See [cutover acceptance](forcing_hourly_cutover_checklist.md). An additional
real-source NLDAS-arrival replay remains useful broader coverage, but is not
claimed as part of these schema/cutover results. Input selection is explicitly
versioned; compression/schema normalization alone does not invalidate baselines.

## September 24 UTC: implicit versus explicit diagnostic fill

Daily-cycle worker 4628622 published September 18–22 but failed September 16–17
when legacy `gfs_fallback_qc` had no `_FillValue` attribute and newer inputs had
explicit unsigned-byte fill 255. The compressed-chunk writer now accepts absent
versus explicit **type-default** fill on canonical diagnostics only, checking
the effective fill of both inputs and output. NetCDF already masks the implicit
default; raw values and missing-value interpretation are therefore preserved.
Other fill differences, physical-field metadata mismatches, and unit conflicts
remain errors. Historical baselines need no rewrite.

Regression coverage includes both source orders, masked QC sentinels, zero-valued
valid QC, intermediate-window/calendar reassembly, and rejection of conflicting
nondefault fill. All 48 focused archive/schema/Stage-IV tests passed.

Stage-IV current-UTC-day directory 404s or empty listings are now logged as
not-yet-published and retried on the next refresh, without marking data complete
or creating placeholder files. Historical/future directory failures and other
HTTP errors still fail. Retry **4629198** succeeded after five current-day files
became available. Recovery controller **4629199** submitted worker **4629200**
for the original September 16–22 tail, reusing unchanged successful publications
where signatures match, then continuing older-window checks. The original failed
cycle record is preserved; recovery state is
`forcing/work/nwm-forcing-cycle-daily-recovery-4628621.json`.
Submission is not a claim that production recovery has passed.
