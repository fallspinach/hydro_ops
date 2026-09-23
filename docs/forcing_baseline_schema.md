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
`forcing/status/layout-migration/operational-gate-4627429/`. Migration remains
blocked until this test passes; the script cannot migrate data or release NWM.

Subsequent operational acceptance should include an unchanged-input no-op cycle
and NLDAS replacing HRRR/GFS. The schema layer itself deliberately does not alter
source fingerprints or skip decisions. Layout migration remains separately gated
on the running NWM job and the migration checklist.
