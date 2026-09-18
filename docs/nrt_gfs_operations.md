# Source-aware recent NRT operations

## Rollout status

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
Set `enabled = false` in the TOML file to disable new use without changing already
published data. Review `forcing/status/nrt-gfs/latest.json` and the activation
receipt rather than assuming configuration alone means the integration is live.

## Scheduling and scope

The repository cron template invokes `bin/update_nwm_forcing.py` at 02, 08, 14
and 20 UTC. On the inspected host, `crontab -l` reported no crontab for mpan;
installation on the intended scheduler host still needs verification. The template
commands need no replacement: the coordinator reads the new
configuration. The 08 UTC pass retains the existing deeper older-window refresh.
The monthly retrospective cycle is unchanged.

The recent path manages the last **seven target days**, covering the usual 3–4-day
NLDAS-2 latency gap plus overlap for source replacement. It runs after the external
source-refresh and initial older-window baseline dependencies, before older-window
convergence. This ordering serializes access to their boundary baseline. A worker
reserves 64 CPUs and 240 GB scratch; four assembly processes do the expensive work.
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

Baseline data remain in `forcing/outputs/conus/baseline` for later retrospective
work. Final recent NRT stays in `forcing/outputs/conus/nrt`; no retro output is
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
