# Daily and monthly forcing summaries

Use `bin/aggregate_forcing.py` from the `hydro-ops` environment. It supports any
domain whose forcing follows the existing `YYYY/MM/YYYYMMDD.LDASIN_DOMAIN1`
layout (also accepts `.nc`). Domain size and mask are read from the files, not
hardcoded to CONUS. These summaries are **not hourly LDASIN model inputs**.

The resolution-directory migration is planned but **not active**; see
[layout and cutover gates](project_layout.md#planned-temporal-resolution-level--not-activated).
After cutover, hourly input roots become `forcing/outputs/<domain>/<stream>/hourly`.
Daily and monthly roots shown below stay siblings of that new hourly directory.
Until cutover, continue using the current hourly roots in these examples.

## UTC intervals and variable semantics

For UTC day D, use the 24 completed endpoint samples **D 01 through D+1 00**.
The physical interval is `[D 00,D+1 00]`, with a time coordinate at D 12.
A monthly interval starts at 00 UTC on its first day and ends at 00 UTC on the
first day of the next month. Its hourly samples likewise begin at 01 UTC and
include the next month's 00 UTC endpoint. Leap years are handled by calendar dates.

This is equivalent to shifting **hourly endpoint labels** back one hour before
calendar grouping, but no source timestamps are changed. A naive CDO `daymean` or
`monmean` on unshifted hourly data selects the wrong samples. `shifttime,-1hour`
can select the intended groups, but by itself does not enforce complete coverage,
variable-specific methods or the output time bounds.
Do not shift already reduced daily data again.

The existing `config/forcing_daily_reducers.toml` controls both frequencies:

| Input | Summary | Units / meaning |
| --- | --- | --- |
| T2D, Q2D, PSFC | Arithmetic mean of hourly endpoint values | Original units |
| T2D → T2D_MIN, T2D_MAX | Minimum and maximum hourly temperature | K; sampled extremes, not continuous-time extremes |
| SWDOWN, LWDOWN | Mean flux | W m-2; not radiation energy totals |
| U2D, V2D → WIND_SPEED | Mean of hourly sqrt(U2D² + V2D²) | Original wind units; direction changes do not cancel speed |
| RAINRATE | Mean precipitation rate | Original kg m-2 s-1 units; no depth variable |

For a month built from daily summaries, mean fields are averaged across complete,
equal-duration 24-hour days. Monthly T2D_MIN is the mean of daily minima and
T2D_MAX is the mean of daily maxima. Both follow PRISM's monthly statistical
definition (average daily extrema), rather than the month's absolute extremes.
When reading hourly inputs directly, the reducer first calculates each complete
UTC day's extrema and then averages across days. Our extrema use hourly samples
and 00–00 UTC operational days; they need not numerically equal PRISM values.
T2D remains the mean of hourly temperatures, not the midpoint of T2D_MIN/T2D_MAX.
Monthly wind speed is the mean of daily mean speeds, not reconstructed from
mean components. U2D/V2D and RAIN_DEPTH are omitted by default; hourly model inputs
remain unchanged. For plotting, precipitation depth in mm is mean RAINRATE
(kg m-2 s-1) multiplied by the time-bound duration in seconds (86,400 for a full
day; actual calendar-month length × 86,400 for a month).
Methods belong to variables (`cell_methods`),
so the file is not labeled a universal “mean” product. Optional methods in the
configuration include minimum, maximum, sum, first, last and omit; specify suitable
output names/units for any additional integrated flux. Explicit legacy integral
configurations remain supported. T2D_MIN, T2D_MAX and WIND_SPEED are virtual input
names resolved from hourly T2D and U2D/V2D before reduction. Daily algorithm v2
files remain compatible. Monthly algorithm v3 invalidates earlier monthly outputs
for `--skip-existing`; regenerate those using `--overwrite`.

## Commands

Example CONUS daily summaries (inclusive start/end dates):

```bash
conda activate hydro-ops
python bin/aggregate_forcing.py \
  --input-root forcing/outputs/conus/retro/hourly \
  --output-root forcing/outputs/conus/retro/daily \
  --frequency daily --start 1981-01-01 --end 1981-01-31 \
  --domain conus --stream retro
```

Input must extend through **1981-02-01 00 UTC**. Outputs are
`daily/YYYY/MM/YYYYMMDD.LDASIN_DOMAIN1.daily`, with one record each.

Build monthly products from those verified daily summaries (preferred when daily
summaries already exist):

```bash
python bin/aggregate_forcing.py \
  --input-root forcing/outputs/conus/retro/daily \
  --output-root forcing/outputs/conus/retro/monthly \
  --frequency monthly --from-daily --start 1981-01-01 --end 1981-01-31 \
  --domain conus --stream retro
```

Or build monthly summaries directly from hourly archives: omit `--from-daily` and
point `--input-root` at `forcing/outputs/conus/retro/hourly`. Output is
`monthly/YYYY/YYYYMM.LDASIN_DOMAIN1.monthly`. Monthly requests require whole months;
there is no automatic partial-month output.

These suffixes follow the NWM `.daily` output naming convention. They are NetCDF
files without an additional `.nc` extension. Despite `LDASIN_DOMAIN1` in the name,
they are summary products, **not valid model forcing inputs**: variable sets and
time resolution differ. Point NWM only at the hourly archive, never these folders.
The `publication_role` attribute retains this warning.

Earlier examples/backfill tasks used `YYYYMMDD.FORCING_DAILY.nc` and
`YYYYMM.FORCING_MONTHLY.nc`. The summary reader temporarily accepts those names;
new publications use `.daily`/`.monthly`. Existing old-named files are reused
rather than duplicated. Both names for the same period cause an error.
`bin/rename_forcing_summaries.py --root <domain/stream>` previews the rename;
`--execute` performs same-directory renames under the backfill lock. Run it only
after all summary writers stop. No field recomputation is required. Monthly
freshness signatures normalize the daily filename change; original provenance
paths and historical benchmark receipts remain records of the paths used then.

For CNRFC or another subset, replace the input/output roots and the `--domain`
label. The label is provenance only—it does not crop or alter the grid. Generate
the subset hourly archive first. These commands are examples, not submissions of
archive-wide production. The original one-day utility `reduce_forcing_model_day.py`
remains available; `aggregate_forcing.py` adds bounded-memory range processing and
compatible daily-to-monthly reuse. The new monthly reuse mode accepts daily files
from this new utility, not arbitrary daily products or legacy one-day outputs.

## Checks, performance and revisions

- Require every endpoint exactly once in order; reject missing, duplicate or
  off-hour samples, unsupported calendars, inconsistent units and grid changes.
- Require compatible algorithms, reducer settings, bounds and sample counts when
  consuming daily summaries. Preserve lat/lon, static grid metadata and subset masks.
- Missing/nonfinite values propagate at each cell; no skipping missing hours in a
  mean, zero-filling precipitation or spatial filling. Complete time coverage does
  not imply every spatial cell is populated (ocean/inactive cells may remain missing).
- Read/write spatial strips (`--block-rows`, default 120), accumulate in float64,
  and publish float32 with lossless DEFLATE level 2. Memory does not scale with
  the number of hours in a month. Multiple source files remain open, with bounded
  per-variable NetCDF caches. Full-CONUS throughput is not benchmarked yet.
- Atomic publication protects prior output on failure. Existing files are refused
  by default. `--skip-existing` skips only matching source path/size/mtime identities,
  algorithm, interval and reducer settings; stale outputs require `--overwrite`.
  This is an identity check, not a new full content checksum of each source archive.
  The specifically planned `forcing/outputs/<domain>/<stream>/hourly/YYYY/MM/`
  insertion is normalized to the existing year path for signature purposes.
  Same-filesystem year-directory renames therefore do not stale the summaries;
  size/mtime changes, different streams/domains and unrelated relocations still do.
  Recorded source paths remain provenance of the original reduction location.
- For NRT, refresh/rebuild changed daily summaries **before** rebuilding monthly
  summaries. Monthly reuse checks its daily inputs, not their original hourly
  ancestors. Do not assume a previously generated monthly NRT product is immutable.
- No source/QC category is numerically averaged. Hourly provenance is retained
  through the source-file identity list, not represented as a mean category code.
- Keep 1979-01-01's incomplete 11-hour source separate. Strict daily/monthly mode
  deliberately rejects a period lacking required endpoints; it does not fabricate
  a full January 1979.

## Verification

Tests exercise year-boundary samples, missing spatial values, leap-February
hourly-versus-daily monthly agreement, incompatible grids, stale/repeat behavior,
incomplete periods and production precipitation-unit notation. A real-data smoke
test uses an 8×8 NWM tile from 1981-01-31 and 1981-02-01 under
`forcing/work/temporal-summary-smoke-20260920/`; it is not a CONUS performance test.

## Audited retrospective backfill and concurrency benchmark

`bin/backfill_forcing_summaries.py` accepts complete-month ranges and bounded
process parallelism (default four). It is currently specialized for audited CONUS
retro archives with the accepted historical static-envelope-v4 manifest schema;
the general `aggregate_forcing.py` remains domain-independent. It checks verified
manifests, accepted audit records and matching published inode/size/mtime before
processing, including the next-day boundary. This reuses existing scientific
audits; it does not claim to repeat their full field checks or recompute hashes.
Rechecking inputs before/after each daily task detects intervening repairs.

Daily and monthly products are written directly to their final sibling directories
under the existing retro root. Only hourly year directories move later. A
cooperative output-root lock prevents overlapping backfill controllers. Run no
independent summary writer against the same destination while it is locked.
With `--parallel-years`, controllers instead hold a shared root lock and exclusive
locks for every output year they touch. Disjoint years may run together; overlapping
years and legacy exclusive-root controllers remain mutually excluded. Next-year
hourly boundary inputs are read-only and are not cleaned up by summaries.
Existing matching summaries are skipped; stale ones fail rather than silently
overwrite accepted products. Monthly processing starts only after all requested
daily tasks succeed, so a failed run cannot publish new incomplete monthly means.

The 1979–1980 and 1986–1990 expansion uses
`slurm/backfill_forcing_summaries_1979_1990.sh`: seven concurrent year tasks,
four workers and eight reserved CPUs per task (28 workers / 56 reserved CPUs).
Expect roughly 7–10 hours after allocation based on earlier single-year runs;
aggregate filesystem contention is not yet benchmarked at seven-year concurrency.
Submitted as array **4617402** on September 22, 2026 (UTC); indices 0–6 map to
1979, 1980, 1986, 1987, 1988, 1989, 1990. Logs are
`forcing/logs/summary-backfill-4617402_<index>.out`. All requested source-day and
next-day-boundary audit checks passed before submission.
1979 starts January 2: January 1 is incomplete, so no January monthly mean is
published. `--skip-incomplete-first-month` explicitly enables this daily-only
initial partial month. Early monthly-constrained source manifests may lack a
top-level `verified` flag; their identity-matched static-envelope audits must then
prove 24 records, no missing active cells, unchanged active/retained values, and
no values outside the mask for all eight forcing fields. Explicit `verified=false`
is never accepted through this compatibility path.

```bash
python bin/backfill_forcing_summaries.py \
  --input-root forcing/outputs/conus/retro/hourly \
  --output-root forcing/outputs/conus/retro \
  --start 1981-01-01 --end 1985-12-31 --audit-only
```

The full-CONUS single-day benchmark 4590082 took 212.95 seconds for reduction,
peaked at 1.31 GiB RSS, and wrote 410 MB.
`slurm/benchmark_forcing_summary_parallel.sh` runs January 1981 at four workers
(final daily/monthly destinations), then eight workers (isolated benchmark output),
and compares every variable/cell in 31 daily files and one monthly file. Timing
records separate daily reduction from total daily-plus-monthly time; GNU time
records resource use. The second run may benefit from filesystem cache warmth;
this is an operational scaling indication, not a controlled cold-cache experiment.
Existing January summaries would invalidate the fresh-work timing acceptance gate.

`slurm/backfill_forcing_summaries_1981_1985.sh` runs one year at a time (`%1`),
four reducer processes per year, after the benchmark succeeds. Submission requires
both an `afterok` dependency and `HYDRO_SUMMARY_BENCHMARK_ACCEPTANCE` pointing to
the passed benchmark receipt. It reuses January summaries and produces monthly
summaries from daily inputs, never rereading hourly data for monthly reduction.
Eight workers are **not** automatically adopted: review shared-filesystem impact
and measured scaling first. No current forcing/model job or cron is altered.

Initial campaign: benchmark **4590341**, followed by backfill array **4590342**
(`0..4` map to `1981..1985`, one year active at a time). The 1981–1985 audit-only
preflight passed for 1,826 days plus 1986-01-01. Benchmark records live under
`forcing/work/summary-parallel-benchmark/job_4590341/`; per-year logs are
`forcing/logs/summary-backfill-4590342_<index>.out`. These are submitted job IDs,
not by themselves a claim of completion. A failed benchmark blocks the dependent array.

### September 21 acceptance update

Benchmark **4590341 passed**: all variables/cells matched across 31 daily files
and one monthly file. Four workers took **27m26s** for daily reductions and
**30m54s** including monthly processing. Eight workers took **14m08s** and
**17m31s**, respectively: **1.94× daily-stage speedup**. The second run's cache
warmth remains a caveat. The accepted backfill conservatively retains four workers.
At the subsequent commit review, years 1981–1984 were completed, 1985 was running,
and suffix-rename job 4590540 remained pending. Consult SLURM and acceptance
records for current completion, not this dated snapshot. Internal hourly layout
migration was still unexecuted at that review; the September 23 cutover is now
complete (see [cutover acceptance](forcing_hourly_cutover_checklist.md)).

### Backfill from 1991 onward (September 23)

`slurm/backfill_forcing_summaries_1991_2026.sh` covers 1991–2025 and January–February
2026. Array index `i` maps to year `1991+i`; index 35 stops at February 28.
March 1 hourly data supply the boundary record needed for February 28's daily
summary. Partial March is deferred rather than publishing an incomplete month.
The targets are **12,843 daily files and 422 monthly files**.

Submitted array **4628364** after all **12,844 input-day audit checks** (including
the final boundary day) passed. Logs are
`forcing/logs/summary-1991-2026-4628364_<index>.out`. Submission is not a completion
claim; per-task `status=completed` records confirm daily and monthly completion.

The array requests eight CPUs per year task, four reduction processes per task,
and at most sixteen concurrent tasks: **128 allocated CPUs / 64 reduction
workers**, plus the separate existing NWM allocation. The established two-CPU
allocation per worker provides memory headroom under this cluster's CPU-based
memory allocation. Each task has a 48-hour limit and holds a disjoint year lock.
Its complete input range, including the following boundary day, must pass the
existing identity-matched source audits before any summaries are written.

Historical post-2020 publications can retain a scratch-file identity in the
content audit. The checker also accepts their explicit checksum-verified transfer
chain: the manifest must match the current permanent file identity, the audit
must match the recorded staged identity, and candidate and mask hashes must
agree. Unlinked identity mismatches and unverified transfers remain rejected.
This compatibility handling does not alter any hourly source data.

Input: `forcing/outputs/conus/retro/hourly/YYYY/MM/`.
Outputs remain siblings: `retro/daily/YYYY/MM/YYYYMMDD.LDASIN_DOMAIN1.daily`
and `retro/monthly/YYYY/YYYYMM.LDASIN_DOMAIN1.monthly`.
Hourly files are read-only. Matching existing summaries are reused; stale ones
cause a failure instead of being overwritten. Monthly summaries are calculated
from the completed daily summaries using the established reducer definitions.
# NRT stream support (September 24, 2026)

The existing annual backfill arrays target **retro only**. The shared backfill
command now also accepts `--stream nrt --complete-months-only` and separate NRT
input/output roots. NRT outputs belong under `forcing/outputs/conus/nrt/daily`
and `forcing/outputs/conus/nrt/monthly`, with the same suffixes and variable
semantics as retro. Do not merge the two streams.

NRT uses identity-matched acceptance receipts, or the older strict static-envelope
audit chain where no receipt exists. An existing stale/failed receipt is rejected.
Unchanged summaries are skipped. Changed dependencies trigger atomic replacement
of daily summaries and, subsequently, their complete monthly summaries.
Daily bounds require all 01–00 UTC endpoints; a partial current-day file can
supply the preceding day's final 00 UTC endpoint but cannot yield a premature
summary of its own day. Monthly products remain complete calendar months only.

Private daily/repeat smoke test: **4635198**, September 20, 2026. Automatic cron
integration is not yet enabled. Recommended cadence is once daily after accepted
NRT publication, on a separate job so reductions do not delay latest-hour forcing.
Inspect the rolling revision window plus previously missing summaries; rebuild
the affected complete months. Initial historical backfill is separate.

An initial source audit accepted 191 of 205 NRT files. April 12 and September
1–13 use other metadata schemes and need acceptance-chain review before broad
summary backfill. This is not evidence of bad forcing values. Do not bypass those
checks just to populate summaries.

Subsequent content checks confirmed excess coverage in April 12 and sampled
September files. Repair job **4635990** now targets those 14 dates; summaries
must use its accepted replacements, not the pre-repair identities.

### Planned operational dependency order (not installed yet)

1. Publish/audit new hourly NRT files first; the six-hourly extension must not
   wait for temporal summaries.
2. After the daily hourly-revision cycle succeeds, launch a separately locked
   NRT summary refresh. Inspect accepted replacements plus missing summary days.
3. A changed hourly file dated D invalidates daily intervals D-1 and D because
   the former uses D's 00 UTC record. Recompute affected summaries only; reject
   incomplete intervals. Rebuild each affected **complete** calendar month from
   current daily summaries, including the previous month at month boundaries.
4. Report hourly and summary freshness separately. A failed refresh must remain
   visibly stale, not be labeled current or hold up model-ready hourly forcing.

Activation remains gated on hourly rollout tests and the NRT summary tests.
The current backfill command supports atomic stale replacement; automated
dependency selection and cron submission still need integration. Do not install
a blind full-history reduction in the six-hourly critical path.

`tests/test_nrt_summary_revision.py` now exercises the actual backfill command
over two months with accepted small-grid NRT input fixtures. It verifies no-op
reuse, an hourly revision spanning a month boundary, exact changed-output sets,
and resulting daily/monthly numerical means. The test passes. It runs reduction
tasks serially for determinism; cluster-scale parallelism is tested separately.
