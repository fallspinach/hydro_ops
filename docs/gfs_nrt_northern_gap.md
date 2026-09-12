# Experimental GFS fallback for the northern NRT gap

Status, 2026-09-12: opt-in exploration and reusable implementation, **not enabled
in production or cron**. Existing NLDAS-2/HRRR processing and active repair jobs
are unchanged. This is a short-forecast fallback, not an hourly GFS analysis.

## Selection policy

Use GFS only when NLDAS-2 is unavailable and the target lacks native HRRR coverage.
Remain inside the common seven-meteorological-field static envelope. Use one GFS
cycle for the complete seven-variable meteorological bundle. Preserve eligible
precipitation from the existing selection pipeline; GFS precipitation is a final
fallback, not an override of supported precipitation. Once NLDAS-2 becomes
available, bypass this fallback and regenerate under the normal source policy.

For a target hour, prefer leads 1–6 from successive 00/06/12/18 UTC cycles.
At cycle boundaries, use the preceding cycle's f006, not f000. If that bundle is
unavailable or fails validation, try the preceding cycle up to a maximum lead of
12 hours. Never mix cycles when deriving statistical increments. No multi-day
forecast is needed to cover a multi-day NLDAS-2 latency window.

For actual as-of selection, the downloader accepts a timezone-aware cutoff and
requires the object Last-Modified (or, conservatively, local retrieval time when
remote metadata is absent) not to exceed it. This checks evidence for availability
on the downloaded archive endpoint, not NOAA's internal release schedule. The
historical week experiments intentionally do **not** claim to reproduce actual
real-time source availability.

## Source fields and interval handling

`src/hydro_ops/download/gfs.py` fetches indexed byte ranges from NOAA's public GFS
archive, rather than whole multi-hundred-MB files. It verifies HTTP 206, byte
ranges, GRIB framing, stable object identity, and decoded valid time. It extracts:

| Field | GFS field |
| --- | --- |
| Temperature, humidity | TMP, SPFH at 2 m |
| Pressure, terrain | PRES, HGT at surface |
| Wind components | UGRD, VGRD at 10 m, earth-relative |
| Downward radiation | DSWRF, DLWRF at surface |
| Precipitation | APCP at surface |

Selected messages are decoded with wgrib2. The regional cache covers 24–54 N,
126–66 W, including a buffer around the NLDAS-2 rectangle. Transfer still includes
the selected **global messages**; geographic cropping occurs locally. Forecast
cache writes are per-file locked and atomic. GRIB intermediates use node scratch.

Live January 2026 inventories show bucket and cycle-total APCP messages, sometimes
with identical interval labels. Select the shortest matching accumulation window,
consistently using the first/bucket message when windows coincide. Radiation means
and bucket precipitation reset at six-hour lead boundaries. For window `(a,b]`:

- Hourly precipitation is accumulated depth at `b` minus accumulated depth at
  `b-1`, only when both start at `a`; a one-hour window is already hourly.
- Hourly radiation is `(b-a)*mean(a,b) - (b-1-a)*mean(a,b-1)` under the same rule.
- Invalid intervals or material negatives reject that candidate bundle. Small
  negative rounding artifacts are clipped and counted; initial tolerances are
  0.05 mm for precipitation and 2 W/m² for radiation, subject to evaluation.

Sources: [NOAA GFS overview](https://www.emc.ncep.noaa.gov/emc/pages/numerical_forecast_systems/gfs.php),
[forecast inventory](https://www.nco.ncep.noaa.gov/pmb/products/gfs/gfs.t00z.pgrb2.0p25.f003.shtml).

## Geometry and downscaling

`src/hydro_ops/forcing/gfs_gap.py` derives native HRRR support using its spherical
Lambert projection (38.5° standard parallel, central longitude -97.5°), calibrated
to the decoded source grid. It verifies projected indices against every HRRR grid
point before evaluating the NWM coordinates. Source-center bounds conservatively
define interpolation support; no nearest-filled output mask is used as evidence.

The verified geometry preparation (job 4519553) found:

- 536,538 retained gap cells, including **501,663 active cells**;
- latitude range 49.43022–52.999996 N;
- 1,990 active cells missing prepared DEM elevation, supplied by the coordinate-
  checked NWM `wrfinput_CONUS_NLDAS2.nc:HGT` field and flagged in the output;
- no remaining active elevation gaps; any such gap would fail preparation.

The gap/weights cache stores target indices, coordinates and grid hashes, active
flags, elevation-source flags, source coordinate arrays, and four-point bilinear
addresses/weights. GFS meteorological values are normalized to reference elevation
using the existing coupled Cosgrove transformations, interpolated, and restored
to target elevation. Wind and shortwave are interpolated directly. No extrapolation
is permitted outside the buffered GFS source grid.

The merge adapter explicitly overwrites all seven fields at geometric gap cells,
even where old nearest filling created finite values. Precipitation replacement
requires an explicit genuine-support mask from the precipitation selector; finite
values alone are insufficient. It returns separate meteorological and precipitation
usage masks for provenance integration. The opt-in daily-copy writer now stores
GFS meteorology as source ID 4 and GFS precipitation as source ID 8, together with
cycle, lead, terrain and humidity-clipping flags. Scheduled production is unchanged.

The original `patches/` experiments use bilinear precipitation and remain only
coverage/interval tests. New `patches_conservative/` experiments and the daily-copy
writer use cached CDO `gencon` weights with `destarea` normalization and no
extrapolation. Actual NWM cell corners define the sparse target footprint.
Weights are checked against source/target identities and complete coverage.
Job 4519683 produced 600,997 links for 536,538 cells: maximum constant-field error
1.02e-8 and random-field overlap-volume relative error 1.95e-16. Conservation is
over this footprint, not the entire GFS source rectangle. All 14 conservative
January/July daily tests passed (array 4519717).

## Reproduction and outputs

```
python bin/explore_gfs_nrt_gap.py --prepare --work /path/to/job/scratch
python bin/explore_gfs_nrt_gap.py --day 2026-01-15 --work /path/to/job/scratch
```

Do not rerun `--prepare` over an existing weights file. Experiments live only in:

```
forcing/work/gfs-nrt-exploration/
  geometry.json
  gfs_hrrr_gap_weights_v1.npz
  cache/YYYYMMDDHH/fFFF.nc
  patches/YYYY/MM/gfs_gap.YYYYMMDD.nc
  patches/YYYY/MM/gfs_gap.YYYYMMDD.json
```

Patch files contain 24 hourly records, grouped 00–23 UTC, on a sparse `cell`
dimension with flat NWM indices. They are **not LDASIN files** and must not be
passed directly to WRF-Hydro. Interval-end bounds accompany hourly mean fluxes;
states retain point-time semantics. Outputs record GFS cycle/lead, terrain-source
flags and source metadata. JSON reports contain hourly ranges and rounding counts.
Level-2 compression is used. No retro/nrt production data are modified.

### Completed first-week results

All 14 daily experiments ultimately passed after the cached-metadata fix:
January 15–21 and July 15–21, 2026. Each contains 24 records, all eight fields,
and complete finite values at all 501,663 required active gap cells. The other
34,875 retained inactive gap cells also have finite values in these experiments.
All 336 hours used forecast leads 1–6. A subsequent archived-availability replay
tested the previous cycle's lead 7 (job 4519850); this was not a live outage.

| Diagnostic over retained gap cells | Winter week | Summer week |
| --- | ---: | ---: |
| Temperature range | 229.91–288.88 K | 269.01–310.48 K |
| Downward shortwave range | 0–362.88 W/m² | 0–937.44 W/m² |
| Maximum hourly precipitation depth | 2.36 mm | 16.76 mm |
| Compressed sparse file size per day | 215.5–226.3 MB | 230.0–238.4 MB |

These are data-path/coverage results, not proof of observational accuracy or seam
quality. The sparse patch runtime is not a benchmark for full CONUS LDASIN
production. Later complete daily-copy and CONUS model-read tests passed (below).

Array 4519560 tests January 15–21 and July 15–21, 2026, with four concurrent
12-CPU tasks and 120 GB scratch reservations. Early workers exposed cached NumPy
integer metadata failing JSON serialization; that defect was fixed and regression-
tested. Retries: 4519577 (index 0), 4519588 (indices 1–3), 4519602 (indices 5–6).
These failures preceded test-file publication. Geometry probes 4519537/4519542
deliberately rejected missing elevation before the explicit model-HGT fallback
was implemented. Eight unit tests cover time windows, cycle fallback, no spatial
extrapolation, source-priority merging, availability cutoffs and cached metadata.

## Gates before enabling NRT

1. Inspect hourly/cycle-boundary jumps and spatial seam maps; assess whether a
   narrow transition is necessary. Paired overlap diagnostics below are not
   independent observational validation or a direct seam-discontinuity test.
2. The full-day copy, archived delayed-cycle and model-read tests passed. Complete
   automatic NLDAS arrival/replacement testing in the operational controller;
   the isolated midnight test verifies a prepared source transition only.
   Unit tests cover cycle fallback,
   cutoff rejection, NLDAS precedence,
   support-preserving rain selection, conservative weight guards and a synthetic
   24-record publication with unchanged-input verification. A synthetic mid-day
   outage also verifies that no incomplete daily output is published.
3. Add coordinated six-hourly acquisition, bounded cache retention, refresh and
   status reporting. The standalone writer does not yet replace the operational
   controller and will not overwrite an existing published file.

## Paired winter/summer comparison

Job 4519716 compared January 15–21 and July 15–21 at 00/06/12/18 UTC (56 times),
with deterministic samples of 2,000 active gap cells and 2,000 active cells within
approximately 45 km inside HRRR's northern boundary. All sources were adjusted
to the same target elevation. Unavailable NLDAS interpolation stencils were
excluded, not extrapolated. Results are in
`forcing/work/gfs-nrt-exploration/overlap_comparison.json`.

| GFS minus reference, temperature | Winter bias / RMSE (K) | Summer bias / RMSE (K) |
| --- | ---: | ---: |
| NLDAS-2, northern gap | -0.67 / 3.46 | -0.93 / 2.63 |
| HRRR, inside-boundary strip | +0.39 / 2.94 | -0.32 / 2.18 |

Summer specific-humidity bias was -1.42 g/kg against NLDAS-2 in the gap and
-0.87 g/kg against HRRR in the inside strip. These differences warrant inspection
before routine adoption; low regional mean bias does not rule out local seams.
GFS radiation is an interval mean whereas HRRR analysis radiation has different
temporal semantics, so radiation differences require particular caution.

## Opt-in full daily writer

`bin/produce_gfs_nrt_day.py` consumes an HRRR-based 00–23 UTC daily file and writes
a separate copy. It uses node scratch, checks target coordinates, inserts the
coupled GFS bundle only in the geometric gap, and preserves eligible precipitation
(IDs 1–5 and 7, finite/nonnegative, without missing-source QC bit 8). Merely finite
HRRR or old nearest-filled precipitation is not treated as supported rainfall.
Provisional GFS precipitation confidence is 0.15, explicitly uncalibrated.

Residual missing active cells outside the northern gap use valid non-GFS primary
donors inside the static envelope. No new inactive-cell holes are filled. Every
field is masked outside the envelope. Per-hour repair counts/distances and GFS
provenance are reported. All 192 field/hour records are reopened and checked for
active completeness, outside-envelope masking and write integrity; pre-existing
valid unselected values inside the envelope are checked for exact preservation.
Transfer to permanent storage is checksum-verified before atomic file rename.
An audit JSON and a daily manifest accompany the file. Original source files are
not edited. Existing parent precipitation/CNRFC policy is inherited, not audited
or repaired by this northern fallback experiment.

Historical test 4519800 uses:

```
python bin/produce_gfs_nrt_day.py \
  --input forcing/outputs/conus/nrt/2026/08/20260824.LDASIN_DOMAIN1 \
  --output forcing/work/gfs-nrt-exploration/full_day/20260824.LDASIN_DOMAIN1 \
  --work /path/to/job/scratch --historical-test
```

Historical mode deliberately skips today's NLDAS availability decision and cannot
write under production outputs. Non-historical operation requires a timezone-aware
`--as-of`; writing a new CONUS NRT production destination additionally requires
`--publish-nrt`. If NLDAS-2 is available for any input hour, the current conservative
guard returns `rebuild_with_nldas2` without publication; the normal builder must
handle that day rather than letting GFS supersede NLDAS. Mixed-availability days
still require integration with the operational per-hour planner.

### Full-day acceptance result

Job **4519800 completed successfully in 6 min 46 s**, with 12 CPUs allocated and
120 GB node scratch reserved. This is an isolated postprocessing-copy test,
including GFS acquisition, merging, full readback and transfer; it is not the
runtime of the upstream forcing-production pipeline. The output is approximately
4.8 GiB, at the `full_day/20260824.LDASIN_DOMAIN1` path above.

- 24 records, 00–23 UTC, all eight fields audited;
- 501,663 active northern cells supplied by the GFS meteorological bundle;
- zero remaining missing active values and zero valid outside-envelope values;
- all originally valid, unselected values inside the envelope unchanged;
- supported precipitation preserved; parent input unchanged;
- residual primary-cell repair distances no greater than 16 grid cells;
- permanent-copy SHA-256:
  `3f29cd631dbf2ddd38d1f9a626d8360882685bb0506f7d2d03c7a5d44a50c744`.

The `.gfs-audit.json` and `.manifest.json` sidecars record all hourly provenance.
The focused GFS/publication/static-mask regression suite passes 22 tests, including
failure-without-publication for a synthetic mid-day outage. Subsequent metadata
hardening (atomic JSON sidecars and exact-hour validation) is covered by the
synthetic publication tests; the full-day job loaded the earlier writer version.

### Archived availability replay

Job 4519850 (`bin/check_gfs_cycle_fallback.py`) passed for valid time
2026-08-24 01 UTC with an as-of cutoff of 02 UTC: the unavailable 00 UTC cycle
was rejected and the 2026-08-23 18 UTC cycle's f007 was selected. An impossible
cutoff of 2026-08-23 00 UTC rejected both candidates explicitly. This uses the
archive's Last-Modified evidence and does not reproduce every operational latency
condition. Results: `forcing/work/gfs-nrt-exploration/cycle_fallback_check.json`.

## Completed CONUS model-read tests

After visual acceptance of August 24, job **4519888** completed an isolated 23-hour
CONUS cold start from 2026-08-24 00 UTC to 23 UTC using the reviewed daily file.
It uses 120 MPI ranks on one node, 120 GB reserved scratch, the NLDAS-bounded
`wrfinput_CONUS_NLDAS2.nc`, terrain/channel routing at 600/600 s, lakes off,
`PCP_PARTITION_OPTION=1`, and `t0OutputFlag=0`. Regular history output is disabled.
Forcing is staged in `YYYY/MM/` on node scratch and read directly as a daily
collection. Model scratch, logs and terminal test restarts are isolated from
operational runs and spin-up restarts.

Results are under `nwm/outputs/tests/conus/gfs_nrt_20260824/job_4519888/`.
`slurm/test_gfs_nwm_conus.sh` requires a successful model sentinel and correctly
timestamped land/hydro restarts; `bin/audit_gfs_nwm_restart.py` checks terminal
soil/land states for all active cells and separately for the northern GFS gap.
These are compatibility/finite-state checks, not validation of cross-border
streamflow from an unspun cold start. The model took **6 min 15 s** (job total
6 min 33 s); the terminal-state audit passed for all active and northern-gap cells.

### Why this first test stops at 23 UTC

A full 00-to-00 run needs August 25 00 UTC forcing. Preparing that adjacent day
(job **4519881**) exposed additional holes *inside* nominal HRRR coverage in the
older August 25 NRT parent. Its 00 UTC primary donor repair reaches approximately
267 grid cells, mainly at southern/coastal domain edges. This is a separate issue
from the geometric northern HRRR gap. The resulting August 25 file is **not
accepted for model use** merely because its finite-value coverage audit passes.
It remains a diagnostic experiment; it is not used by 4519888.

The original dependent 24-hour model job 4519882 was held and canceled before
execution. The 23-hour replacement avoids inventing or repeating the boundary
forcing. Its non-midnight terminal restarts are isolated test artifacts, not an
operational restart schedule. Production operations remain 00-to-00 UTC; a full
midnight-boundary acceptance test subsequently passed after the adjacent-day
repair described below. Operational acceptance also needs an explicit guard against
such unexpectedly long primary-donor repairs, not just a finite-value audit.

## Native-donor diagnosis and isolated rebuild (2026-09-12)

**Correction to the initial August 25 interpretation:** the file-level label says
HRRR, but its hourly cell-source IDs identify NLDAS-2. The baseline's own metadata
also identifies NLDAS-2. Inherited global attributes are not authoritative across
source transitions. The experimental GFS writer now examines every hour's cell
provenance, even in historical mode, and refuses to replace NLDAS/hybrid or mixed
hours. Mixed-source days currently fail closed and require native-source routing;
they are not silently treated as all-HRRR days. The new pilot refreshes file-level
source summaries from the actual hourly IDs.

NLDAS-2 does have a valid native donor near Guadalupe Island, but surrounding
missing source cells prevent a bilinear mapping to the example target pixel.
The cached NLDAS weights have no row for that target; HRRR has four valid weighted
donors. Repairing only the already-remapped grid therefore overlooks usable native
data and reaches the distant mainland. This is a remapping/fallback issue, not
proof that the island lacks meteorological coverage.

Exact August 23–27 NLDAS-2 inputs were downloaded into
`forcing/work/native-donor-pilot-20260825/inputs/nldas2/` (4519925, 4520007).
The exact August 25 00 UTC check found 226 Guadalupe active cells needing donors
within **10.27 km**, rather than the old approximately 267 km target-grid fill.
Only 13 other thermodynamic targets exceed 25 km, near the Gulf of California;
the maximum native distance is **35.234 km**. The first 25 km probe (4520008)
correctly rejected these. The explicitly configured **40 km pilot cap** passed
the full real-hour readback probe (4520011). General library default remains
25 km; no operational default or scheduled job was changed.

`src/hydro_ops/forcing/native_donor.py` adds a staged-hour post-remapping repair:

- Prefer exact-hour NLDAS-2, then HRRR through the existing hourly selector.
- Fill only missing active targets, using nearest **native** donors measured by
  great-circle distance; fail if a required donor exceeds the configured limit.
- Repair temperature/pressure/humidity/longwave together using common valid
  donors, reference-elevation transformations and target-elevation restoration.
  Keep wind components together; rotate HRRR winds to earth-relative first.
- Preserve supported precipitation. Only missing precipitation receives native
  hourly-depth/rate fallback, explicitly flagged as fallback (not conservative
  remapping). PRISM reconciliation runs afterward.
- Keep inactive values within the approved envelope, create no new inactive fills,
  and mask everything outside it. Coordinate-checked model HGT supplies missing
  active DEM values and is separately flagged.
- Record per-cell `native_donor_qc` and `native_donor_distance_km`, exact hourly
  source paths, repair counts and maximum distances. Reopen every field before
  accepting each staged hour; daily aggregation also verifies stored records.

The probe repaired 57,934 coupled targets, 43,136 wind targets, 34,700 shortwave
targets and 3,019 precipitation targets. Maximum precipitation donor distance was
9.40 km; the other group maxima were 35.234 km. Thirty focused regression tests
pass, including isolated native donors, elevation adjustment, distance rejection,
preservation of supported rain and rejection of a misleading global HRRR label.

### Rebuild and midnight test workflow

| Job | Scope and acceptance gate |
| --- | --- |
| 4520062, array 0–2 | Rebuild August 24–26 baseline from native sources; 32 CPUs/task, four assembly workers, 120 GB scratch/task. Repair staged hourly files and publish verified daily archives only under the pilot root. |
| 4520066 | After all baseline days pass, apply provisional PRISM windows August 25/26 in scratch and publish **August 25 00–23 UTC** NRT. Recheck all eight fields, source provenance and donor distances. 64 CPUs reserved for memory, 120 GB scratch. |
| 4520067 | After the NRT acceptance audit passes, run August 24 00 UTC to August 25 00 UTC on 120 MPI ranks. Use reviewed GFS-August-24 forcing plus newly repaired NLDAS/PRISM August-25 boundary forcing. Check terminal restart time and northern active states. |

The workflow is implemented by `bin/rebuild_native_donor_pilot.py`,
`bin/finalize_native_donor_pilot.py` and `slurm/test_native_donor_nwm_conus.sh`.
Outputs are under `forcing/work/native-donor-pilot-20260825/{baseline,nrt}/YYYY/MM/`;
model results under `nwm/outputs/tests/conus/native_donor_20260825/job_4520067/`.
Existing production archives, ongoing repairs and operational restarts are not
modified. Downstream jobs use `afterok` dependencies and fail closed if any acceptance gate
fails. The older rejected August-25 GFS test file is not an input to this chain.

### August 25/26 schema failures and retries

Array task 4520062_2 completed all 24 native-donor repairs but failed daily
aggregation at 19 UTC. `precip_timing_source_id` is optional in the precipitation
assembler: Stage-IV six-hour reconciliation creates it, while unreconciled hours
may omit it. That changes the hourly variable list, which the strict daily archive
validator correctly rejects. This is a provenance/schema problem, not a failed
meteorological repair or a reason to loosen the physical-data checks.

The isolated pilot now uses `ensure_precipitation_timing` from
`src/hydro_ops/forcing/pilot_schema.py` to add a zero-valued timing-source variable
where absent; zero denotes no separate within-block timing provenance. Existing
timing IDs and all physical fields are preserved. The pilot normalizes staged
hourly files before aggregation and its completed neighboring daily archives
before PRISM windows are assembled. No production or retro caller invokes this
helper. Schema validation remains strict, with improved missing/extra-variable
diagnostics. The targeted regression suite passes **41 tests**, including a
reproduction of mixed reconciled/unreconciled hours and exact rainfall preservation.

The original August 25 worker also encountered the schema mismatch. August 26
retry **4520109_2** completed in 22 min 12 s; August 25 retry **4520123_1** completed
in 46 min 22 s. Original August 24 task **4520063** completed in 48 min 26 s.
Dependencies were updated to these successful tasks before downstream acceptance.

### Completed midnight-boundary acceptance

PRISM job **4520066** completed successfully in **23 min 39 s**. Its final file is:

```
forcing/work/native-donor-pilot-20260825/nrt/2026/08/20260825.LDASIN_DOMAIN1
```

All 24 hours passed the native acceptance audit. The maximum native donor distance
was **35.234 km**, within the explicitly configured 40 km pilot cap; the 226
Guadalupe cells needed at most **10.270 km**. The file covers **00–23 UTC**;
12–12 UTC PRISM windows are scratch intermediates, not published file boundaries.

Model job **4520067** completed with exit code 0: **6 min 27 s model time**, or
**6 min 51 s total**, on 120 MPI ranks. It ran August 24 00 UTC to August 25 00 UTC,
reading the reviewed GFS/HRRR August 24 file and repaired NLDAS-2/provisional-PRISM
August 25 endpoint. Both terminal restart timestamps are August 25 00 UTC.
`restart-audit.json` reports zero missing/nonfinite values for SOIL_T, SMC, SH2O,
TG, TV, TAH and ACCPRCP over **10,315,371 active cells**, including all **501,663
northern-gap active cells**. Logs retain the known nonfatal LDASIN version and
missing CHAN_DEPTH warnings. Regular hourly history was disabled.

This verifies forcing ingestion, the prepared midnight source transition and
finite terminal land states. It does not establish hydrologic accuracy, spin-up
convergence, automatic source replacement, or operational mixed-source-day support.
The GFS writer and native-donor pilot remain opt-in; no cron or retro archive was
changed by this experiment.

Pre-commit verification after these changes: the complete repository suite passed
**232 tests** (one import-loader deprecation warning), and Ruff passed across
`src`, `tests`, `bin` and `slurm`. These checks supplement, rather than replace,
the real-data and model acceptance results above.

Scope clarification: this is an isolated NRT compatibility experiment. Guadalupe's
offshore donor distance does not justify reprocessing the retro stream. Operational
coverage priorities are US territory and hydrologically connected upstream areas;
any later model-mask refinement must preserve cross-border contributing watersheds.
