# Source-aware recent NRT operations

## Rollout status

The NLDAS-2 Earthdata Cloud backend passed acceptance and became the operational
default on September 24, 2026 UTC; see
[cloud migration and compatibility checks](nldas_earthdata_cloud_migration.md).
No crontab change is required; the configured discovery backend is now `cmr`.

### Latest NLDAS-2 hours (September 24, 2026 UTC)

Operational source refreshes now submit NLDAS-2 with `--discover-latest`.
The configured five-day lag remains the conservative completeness/repair cutoff,
not the acquisition ceiling: discovery continues through today UTC. Directory
404s beyond that cutoff are logged as not-yet-published and retried next cycle.
Older missing directories, authentication errors, non-404 HTTP errors, and empty
or unrecognized listings still fail visibly. Explicit historical downloads retain
their strict requested ranges unless this flag is supplied.

Available hours from a partial day are retained individually and immediately
eligible for per-hour NRT source selection. A complete 24-hour day is aggregated
with the existing verified daily writer under `YYYY/`; hourly copies are retained
for the existing retention/cleanup workflow. No partial daily collection is
published. NLDAS arrival changes source fingerprints, replacing HRRR/GFS only
where NLDAS is now available; other hours retain HRRR plus northern GFS fallback.
Daily and six-hourly cycles inherit this acquisition policy through
`bin/update_forcing.py`; no crontab change is required.

The earlier fill-metadata recovery **4629200 passed in 30m33s**: September 16–17
were published and September 18–22 were unchanged. Parent **4629199** completed
in 34m08s with zero unresolved days. Its latency report deliberately retains the
original failed cycle's launch time, so it is not a fresh-cycle latency benchmark.
Latest-hour acquisition test **4629219 passed in 20 seconds**: September 19 was
archived, September 20's 13 available hours (00–12 UTC) were downloaded and kept
hourly, and September 21–24 directory 404s were deferred. Real source selection
confirmed 13 NLDAS hours and 11 HRRR hours on September 20. All **393 tests passed**.
Production replacement test **4629221** covers September 19–21, including adjacent
PRISM-window dependencies; its completion/acceptance is still pending.

Sections describing individual experiments below are chronological records.
Current production defaults are the adopted settings in `config/nrt_gfs.toml`,
not the earlier opt-in benchmark settings. For the latest tested timings and
outstanding cron/model gates, see [reliability acceptance](nrt_reliability_acceptance.md).

The production integration is implemented and requested in `config/nrt_gfs.toml`.
**Integration acceptance rerun 4551871 passed** on 2026-09-17 in 3:56:02.
Its activation receipt exists and the coordinator now enables the recent GFS path.
Mixed-hour selection, NLDAS replacement, PRISM publication and unchanged repeat
all passed. This does not establish the sub-hour operational latency target.
No current repair
job was canceled, resubmitted, or configured to use GFS as part of this integration.

Original acceptance job 4520199 failed during PRISM temperature processing:
non-GFS hours used implicit NetCDF fill in `gfs_forecast_reference_time`, without
an explicit `_FillValue` attribute. Xarray interpreted that sentinel as an enormous
date and overflowed. The writer now declares a NaN `_FillValue` and the calendar;
the real-data test explicitly exercises CF decoding for both mixed-source and
all-NLDAS replacement files. All 23 focused GFS/NRT tests passed before resubmission.
The rerun uses 64 CPUs, 240000 MB scratch and a 48-hour limit; it rebuilds isolated
test outputs from scratch, without modifying operational forcing. Failures now
write a failed acceptance status and error rather than leaving a running status.

The acceptance worker uses only isolated output/baseline directories under
`forcing/work/nrt-gfs-cycle-validation/job_4551871/`. It deliberately selects
NLDAS-2 for August 25 hours 00–11 and HRRR for hours 12–23, then restores normal
selection to simulate NLDAS-2 arrival. It checks 12 GFS hours followed by zero,
applies PRISM daily constraints, and repeats an unchanged update to verify reuse.
This controlled source selection is not a historical availability reconstruction.
Every staged baseline and final file undergoes all-eight-field readback checks.

On success, the test atomically writes
`forcing/status/nrt-gfs/activation.json`. The coordinator then enables this path
automatically on its next scheduled NRT cycle. Failure leaves the gate closed.
GFS northern fallback is mandatory for operational NRT. Setting `enabled = false`,
removing the acceptance receipt, or failing activation blocks NRT scheduling;
none of these selects the legacy HRRR-only path. The convergence controller also
rejects older NRT plans that omit the required recent path. Missing GFS bundles
fail the affected update while preserving previously accepted files. NLDAS-only
hours still do not download or use GFS. Retro processing is unchanged.
Review `forcing/status/nrt-gfs/latest.json` and the activation
receipt rather than assuming configuration alone means the integration is live.

## NRT baseline to retro storage handoff

`tests/test_nrt_retro_handoff.py` exercises three retained-baseline layouts:
source-preserved NRT chunks, ordinary older chunks, and mixed generations. It runs
the actual precipitation reconciliation CLI with stable PRISM fixture data, then
the calendar publication CLI in retro mode. Checks cover the corrected rain rate,
unchanged other variables and times, stable revision provenance, and unchanged
baseline input bytes. Mixed encodings must safely fall back to ordinary copying.
This is a small-grid integration test, not a CONUS throughput, coupled-temperature,
source-replacement, or cleanup acceptance test. Baseline cleanup remains gated by
the existing retro publication audits and neighboring-day dependency protections.
No historical files need rechunking solely for this handoff.

## Scheduling and scope

Cross-cycle PRISM window caching is enabled by `window_cache_enabled` in
`config/nrt_gfs.toml`, following paired test 4561354. Each NRT output root has
its own hidden `.prism-window-cache`; these are disposable intermediate windows,
not published calendar-day forcing. Private test roots do not use the operational
cache. An explicit empty `HYDRO_OPS_NRT_WINDOW_CACHE` disables caching for reference
benchmarks; a nonempty value overrides its location.

After window processing, cleanup limits recognized cache entries to 32, 160 GB,
and 14 days since last use, evicting oldest entries first. Limits can be temporarily
exceeded while a window is published. Shared reader/publisher locking and exclusive
cleanup locking protect in-flight copies; cleanup removes only recognized entry
files, never forcing outputs, baseline files, or unknown directory content.
Cleanup is activity-triggered, not a separate cron service. Disabling caching does
not automatically delete retained entries. Cache tuning does not invalidate baseline
fingerprints. Final publication validation is unchanged.

The repository cron template invokes `bin/update_nwm_forcing.py` at 02:30, 08:30,
14:30 and 20:30 UTC. On the inspected host, `crontab -l` reported no crontab for mpan;
installation on the intended scheduler host still needs verification. The template
commands need no replacement: the coordinator reads the new
configuration. The 02:30 UTC daily pass extends first, then performs the deeper
source refresh and revision pass; the other slots only extend latest hours.
The monthly retrospective cycle is unchanged.

The recent path manages the last **seven target days**, covering the usual 3–4-day
NLDAS-2 latency gap plus overlap for source replacement. It runs after the external
source-refresh and initial older-window baseline dependencies, before older-window
convergence. This ordering serializes access to their boundary baseline. A worker
reserves 64 CPUs and 240 GB scratch; baseline production uses four precipitation
remapping workers and eight hourly assembly processes (validated by job 4578181).
Worker-count tuning preserves historical baseline fingerprints rather than forcing
accepted days to rebuild. Rollback values are one precipitation-remapping worker
and four assembly workers in `config/nrt_gfs.toml`.
Native-donor repair now uses four independent spawned processes, and GFS
publication rewrites only changed chunks while retaining all full-field audits
(paired comparison 4578828 passed). Rollback settings are
`native_repair_workers = 1` and `gfs_sparse_writes = false`. These performance
settings do not invalidate previously accepted baselines. Benchmark environment
overrides still take precedence over configuration.
The existing coordinator lock and active-cycle check prevent overlapping scheduled
NRT cycles, and the recent writer takes its own exclusive lock.

The existing complete-day cutoff is retained: target end is UTC today minus two
days, with next-day baseline input available for PRISM windows. This does **not**
introduce partial-current-day publication or remove the existing source-download
latency. Four update opportunities per day do not imply four partial daily files.
All final files still contain exactly 00–23 UTC; PRISM 12–12 UTC windows exist only
in scratch. Model daily accumulation boundaries remain a separate concern.

## Acquisition and hourly selection

For each hour, select a structurally valid, exact-time NLDAS-2 bundle first, then
HRRR. Daily source changes are allowed: contiguous runs of the same source retain
batched remapping. A typical transition day needs two meteorological remaps instead
of abandoning batching for 24 individual remaps. The precipitation selection and
CNRFC policy remain in the existing daily precipitation processor; its halo remap
is repeated when a primary-source transition splits a day.

Only HRRR-selected hours acquire GFS. Acquisition happens before expensive baseline
assembly, caches decoded surface bundles in `forcing/inputs/noaa/gfs/surface_025`,
and uses node scratch for GRIB intermediates. Existing downloader rules still apply:
indexed ranges, whole-bundle cycle identity, valid-time verification, interval-aware
precipitation/radiation decoding, preferred leads 1–6, fallback through lead 12,
and archive Last-Modified evidence at the worker's fixed aware as-of cutoff. These
are short forecasts, not hourly GFS analyses. A missing/invalid GFS bundle rejects
the update; it does not publish a partially filled daily file.

Native-source repair first fills only missing active cells, with a 40 km donor cap
and the approved static envelope. For HRRR hours, it defers the geometric northern
gap to GFS, preventing distant mainland donors from filling that gap first. GFS
supplies the coupled meteorological bundle there; supported precipitation is kept,
with conservative GFS rainfall only where no supported precipitation exists.
Unbounded target-grid repair is forbidden in this operational path. NLDAS-2 hours
receive no GFS substitution, including on mixed-source days.

Verified geometry and conservative weights have been copied—not moved—to
`forcing/static/remapping/nwm_conus_1km/`. Original experimental assets remain
untouched. File SHA-256 values of the production copies match the originals:

- `gfs_hrrr_gap_weights_v1.npz`:
  `84402c46b0d5411b017f3aaf2e3287409af3b83d6cf5155f8b6b01637de84428`
- `gfs_gap_conservative_v1.nc`:
  `57e78f4d807506cd759543684a73e1bc25f3c44a64547774c95d7163faa5530e`

## Replacement, PRISM and publication

Each baseline records hourly primary selections, selected GFS bundles, input-file
sizes/mtimes, relevant static assets and configuration in an input fingerprint.
An atomic `.nrt-receipt.json` also records output identity, SHA-256, donor diagnostics,
cycles/leads and source provenance. Unchanged receipts reuse verified files.
Arrival of NLDAS-2 changes the hourly selection and fingerprint, rebuilding the
baseline and its dependent NRT output. GFS older-cycle selections are revisited.
A retained NLDAS baseline is not downgraded to HRRR during a source outage.

Final fingerprints include the three prerequisite baseline versions and both
PRISM windows' ppt/tmin/tmax files. New or revised MRMS/Stage-IV/source files change
baseline fingerprints; newly available or revised PRISM files change final ones.
When both PRISM windows are available, reconcile them and recombine into a calendar
day. Before they are available, publish an explicitly unconstrained NRT baseline
copy; do not pretend a PRISM constraint was applied. A previously constrained
output is retained if its PRISM inputs subsequently disappear.

Baseline data remain in `forcing/outputs/conus/baseline/hourly` for later retrospective
work. Final recent NRT stays in `forcing/outputs/conus/nrt/hourly`; no retro output is
written. Private candidates are checked for complete active coverage and masked
outside the static envelope after PRISM. Permanent transfers are checksum-verified
before atomic replacement. Failed days keep their previous publication, record an
error and retry during the next cycle. Other days can still complete.

Fallback receipts remain discoverable after aging out of the seven-day lookback.
When NLDAS-2 becomes available, up to two older replacement days are added per cycle
to bound catch-up cost. This queue contains only files made by the new NRT path,
not arbitrary historical or ongoing repair products.

## Status and operational checks

`bin/report_forcing_status.py` now includes `recent_nrt_gfs` in JSON and an activation/
last-cycle line in terminal output. The existing two-hour status cron picks this up
without another job. JSON includes the activation result, current cycle job ID,
per-day publication/reuse, GFS-hour counts, PRISM status, replacement backlog and
errors. Failed recent cycles contribute to the dashboard's attention status.
Completed cycle reports are retained alongside `latest.json`.

Unit/regression coverage includes mixed-hour selection, no GFS acquisition for
NLDAS hours, input preservation, outage rejection without publication, source/PRISM
fingerprint changes, delayed-cycle retry, exact UTC-day validation and activation
gating. The real-data replacement/PRISM chain passed in job 4551871; operational
throughput remains to be measured.

## Real-availability operational test (2026-09-17)

`bin/submit_nrt_operational_test.py` previews a two-day test, or submits it with
`--submit`. It runs the normal external-source refresh with a 14-day completeness
lookback and waits for newly submitted or already active source jobs. As in the
production coordinator, dependencies use `afterany`: source availability is
checked by the worker rather than assuming a downloader exit code implies coverage.
GFS is acquired on demand only for actual HRRR-selected hours.

Job **4553105** runs the first test after source-refresh jobs **4553100–4553104**
(NLDAS-2, Stage-IV, PRISM, HRRR, MRMS respectively). Its campaign is
`forcing/work/nrt-operational-test-20260917T173721/`.
The first test targets September 14–15, matching the coordinator's current
complete-day cutoff of UTC today minus two days. There are no source-selector
overrides. Each test writes its own baseline, NRT, status, and acceptance files in
`forcing/work/nrt-operational-test-<timestamp>/`, leaving production outputs,
production status, retro repairs and activation receipts untouched. The explicit
`state_root` override isolates both the status reports and cycle lock; production
defaults are unchanged.

One 64-CPU worker with 240000 MB scratch runs a cold two-day cycle and then repeats
it immediately. Reports separately record refresh/dependency/queue time and worker
time, compare both against the one-hour deadline, count actual GFS hours, and
verify that unchanged final file identities remain identical. Functional success
does not imply the latency goal was met. Actual source changes or preferred-GFS
cycle arrivals during the repeat can legitimately invalidate reuse and must be
distinguished from a regression. Full seven-day throughput, deeper daily refresh,
and cron installation remain subsequent rollout checks, not claims of this test.

The focused 40-test suite also exercises isolated status, overlapping-cycle lock
rejection, failure reporting, fingerprints, source/PRISM changes and GFS outage
publication guards. Mocked failure tests are not evidence of a real cluster outage.

### Results and optimization experiments

Job 4553105 passed functional acceptance: September 14–15 each used GFS for 24
hours and received PRISM constraints. Cold processing took 191 minutes; including
refresh and waiting, 202 minutes. The unchanged repeat took 46 seconds and did not
rewrite outputs. These results do not meet the changed-cycle one-hour target.

The Stage-IV source job failed on an unpublished stable archive (September 9).
Rolling refresh now passes the existing `--allow-missing` option for Stage-IV only,
logging unpublished days while continuing; network/conversion errors remain fatal.
Retry job **4555696** completed in 3:36. The missing archive had become available,
so this retry did not exercise the skip; regression tests cover the option wiring.

Paired benchmark **4555698** followed that refresh and compared reference writing
with chunk-aware PRISM/calendar writing and opt-in intra-worker PRISM window reuse
(`HYDRO_OPS_NRT_REUSE_WINDOWS=1`). The shared window is keyed by both baseline
checksums, PRISM identities, revision and writer mode, plus its own file identity.
It is stored on the worker's scratch. The validated profile is now adopted as
described below; the scientific algorithms and acceptance checks are unchanged.

The benchmark first warms private baseline copies after refresh, outside measured
arms. It then invalidates only private final receipts to replay a PRISM revision
notification without changing source values. Both arms reconcile the same dates,
repeat unchanged, and undergo decoded-byte/variable-attribute/policy comparisons.
Any baseline rewrite after preparation rejects timing comparability. This is a
controlled reconciliation replay, not a physical rainfall-value revision or an
NLDAS-arrival experiment. Results live in
`forcing/work/nrt-reconciliation-benchmark-20260917T223802/`.

Job **4555699**, after that benchmark, profiles a separate cold September 15 cycle
under `forcing/work/nrt-cold-profile-20260917T2238/`. Python profiles and subprocess
timings separate remapping, GFS publication, PRISM, validation and I/O costs.
Its instrumented runtime and deliberate dependency wait are not clean throughput
measurements. Initial benchmark 4555697 was canceled after 33 seconds to place the
replacement behind source refresh; its private artifacts are not accepted results.
The focused regression suite passed 34 tests before these experiments.

### Adopted reconciliation checkpoint

Benchmark 4555698 passed exact decoded-field, variable-attribute and selected
policy-metadata comparisons for both days. At the same 64 CPUs, two-day
reconciliation fell from **49:36 to 29:28** (40.6% less time); unchanged repeats
took 44 and 41 seconds. Neither measured arm rebuilt baselines. Reusing the shared
PRISM window reduced window calculations from four to three; calendar assembly
fell from 7:54 to 1:49. The separate baseline preparation cost was excluded from
these timings. This is not evidence that a full source-change cycle fits one hour.

`config/nrt_gfs.toml` now selects
`reconciliation_writer_profile = "validated_chunks_reuse_v1"`. The NRT engine
sets chunk-aware writing only for its PRISM/calendar subprocesses and uses the
dependency-keyed scratch window cache. Baseline writers and validation are
unchanged. The new setting is excluded from baseline fingerprints so enabling it
does not trigger pointless baseline rebuilding; existing accepted final files
also remain reusable. Future necessary reconciliations use the new profile.

Rollback: set the profile to `"reference"`. Explicit
`HYDRO_OPS_ARCHIVE_CHUNKS=0` and `HYDRO_OPS_NRT_REUSE_WINDOWS=0` overrides are
also retained for paired benchmarks. No cron installation, new production jobs,
or historical-output rewrite is part of this adoption.

Cold profile 4555699 passed, but took roughly 129 minutes for one final day and
three supporting baseline days. Baseline work accounted for about 105 minutes:
42 minutes initial generation, 29 daily aggregation, 17 native repair, and 15 GFS
publication (rounded, profiled measurements). The next optimization phase should
target baseline aggregation and repeated field I/O, not relax scientific checks.

### Baseline aggregation experiment

Job **4556540** runs `bin/benchmark_nrt_baseline.py` on September 10 (all NLDAS-2)
and September 15 (all HRRR, with GFS gap publication), after checking actual hourly
source selection. Results are isolated under
`forcing/work/nrt-baseline-archive-benchmark-20260918T060130/`.

The worker generates and repairs each hourly input set only once, then runs the
reference and compressed-chunk daily writers on identical files. Writer order is
reversed on the second day. It requires the chunk path to execute (fallback is not
counted as optimization), checks every decoded variable and its attributes plus
selected policy attributes, and rejects any input identity change. After equality
passes, the optimized archive proceeds through the unchanged GFS/domain publication
checks into the private baseline directory. Scratch reference archives disappear
with the normal temporary-directory cleanup; timings and accepted private final
baselines remain available.

The job reserves 64 CPUs and 240000 MB scratch, with a 24-hour limit. Eighteen focused
archive tests passed before submission. Individual writer timings are comparable;
whole-job timing includes two writers and extra comparison reads, so is not a
production throughput measurement. This experiment does not change production
baseline settings or scientific/validation policy. Baseline aggregation adoption
requires reviewing its results first.

The first attempt (4556540) failed safely after 33 minutes: hourly meteorology
uses `(1,120,288)` chunks, whereas the archive writer requested `(1,256,256)`.
Native-donor diagnostics also have automatically chosen, larger spatial chunks.
No valid speedup was measured and the HRRR case was not reached.

Rerun **4556629** opts into `preserve_source_chunks=True` in the chunk archive
API. Multidimensional variables retain each first input's chunk shape; raw copying
still requires matching encodings across inputs and one-record time chunks. Small
one-dimensional coordinates are decoded/copied, handling automatic unlimited-time
chunks larger than the daily destination. Filter/metadata checks, compressed-byte
integrity verification, source-identity checks and publication checksums remain.
Incompatible layouts still fall back safely; the benchmark rejects any such fallback.

Results and input-encoding diagnostics are saved under
`forcing/work/nrt-baseline-source-chunks-20260918T064744/`. The benchmark retains the
same two dates, reversed writer order, 64 CPUs and 240000 MB scratch. Twenty-five
focused tests passed before submission. This is an opt-in experiment: existing
archive callers and operational NRT baseline production retain their defaults.

### Adopted baseline aggregation checkpoint

Job **4556629** passed both paired cases and downstream baseline publication
checks. NLDAS-2 aggregation fell from **587.6 to 62.4 seconds**; HRRR aggregation
fell from **576.4 to 61.9 seconds** (about 9.4x faster and 89% less aggregation
time). All decoded variables and variable attributes matched exactly, as did the
checked policy metadata. Writer order was reversed between days. Aggregated files
were about 1% larger. Total benchmark runtime (1:11:45) includes both writers,
extra comparisons, input generation and downstream processing, so is not a normal
production-cycle runtime.

`config/nrt_gfs.toml` now selects
`baseline_writer_profile = "validated_source_chunks_v1"`. New or legitimately
invalidated baselines in `RecentNrt` use compressed-chunk assembly while preserving
compatible source chunk shapes. Unsupported encodings retain the existing
value-based fallback. GFS publication, active-cell checks and transfer checksums
are unchanged. Receipts record the requested profile, actual archive writer and
archive timing, allowing fallback to be detected in production.

This writer-only setting is excluded from source fingerprints; adoption or rollback
does not invalidate existing accepted baselines or force historical rewrites.
Set `baseline_writer_profile = "reference"` to roll back. Other archive callers,
retrospective campaigns, hourly encodings, CPU allocations and cron installation
are unchanged. The paired benchmark explicitly selects both arms regardless of
the production setting. Full-cycle latency with both adopted optimizations still
needs measurement; the aggregation result alone does not establish a sub-hour SLA.

### Combined operational acceptance gate

Before pushing this checkpoint, the isolated two-day cold update and unchanged
repeat must exercise both adopted profiles. Baseline receipts now expose the actual
archive writer; final receipts also expose both PRISM-window writers and the calendar
writer. The acceptance runner's `require_optimized_writers` plan flag rejects silent
fallback at any of those stages and checks that neither baselines nor final outputs
are rewritten during the unchanged repeat.

Source-chunk preservation must propagate through the NRT PRISM and calendar
subprocesses as well as baseline aggregation. The NRT reconciliation environment now
sets `HYDRO_OPS_ARCHIVE_PRESERVE_SOURCE_CHUNKS` consistently with chunk-aware writing;
other callers retain the previous default unless explicitly opted in. Window-cache
keys include this setting. Regression coverage exercises value overrides followed
by daily reassembly on nonstandard source chunk shapes. This combined path still
requires the full operational run; it is not claimed validated solely from unit tests.

The authorized checkpoint workflow uses a dependent finalization job. It records
the measured outcome and pushes only after the combined writer/reuse test passes,
the repository remains at the tested checkpoint on `main`, and tracked/index changes
are absent. It stages only its dedicated acceptance summary. Failed validation,
repository changes or Git authentication failure leave a `finalization.json` record
and withhold the push. Missing the performance target alone is documented rather
than misrepresented as a correctness failure.
# September 24 efficiency adoption and latest-hour work

The scheduler integration now routes six-hourly NRT through latest-hour
extension and daily NRT through extension → revision cycle → separate summary
refresh. See [current scheduling contract](nrt_operational_extension_schedule.md).
Earlier rollout notes below record historical gates, not the current routing.

NRT baseline workers are now **16 assembly / 8 precipitation remapping / 8 native
repair** following benchmark 4635991 (12.55% less baseline time, equivalent
fields). New recent-NRT workers request 128 CPUs and 240000 MB scratch. The
latest-hour cron activation remains gated separately; this tuning does not
remove any validation or change forcing science.

Mixed-source baseline production now shares the precipitation remapping window
across NLDAS/HRRR segments. Final auditing retains full validation/readback but
only rewrites records requiring changes. New publication days precede revisions.
The full test suite passed (412 tests); isolated integration job **4631009** tests
the complete PRISM cycle and unchanged repeat. Its receipt will be
`forcing/work/nrt-efficiency-adoption-4631009/acceptance.json`.

Latest-hour publication is **not activated yet**. The target is the latest
contiguous usable hour, not a PRISM/MRMS-pass-2 release or midnight boundary.
Required northern GFS coverage and active-cell completeness still apply.
Partial-day acquisition/publication and model-reader tests remain prerequisites
to changing cron's fixed end-date cutoff. See
[efficiency results and rollout](nrt_efficiency_experiment.md).
The explicit partial-day writer is now implemented behind a non-cron API;
[latest-hour rollout and remaining gates](nrt_latest_hour_publication.md) describes
private integration test **4631499** and its limitations.
