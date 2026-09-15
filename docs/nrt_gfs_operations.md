# Source-aware recent NRT operations

## Rollout status

The production integration is implemented and requested in `config/nrt_gfs.toml`.
**Scheduled activation is gated on the real-data acceptance job 4520199**, which
was submitted on 2026-09-12 and is not yet claimed successful. Until its acceptance
receipt exists, the coordinator retains the previous workflow. No current repair
job was canceled, resubmitted, or configured to use GFS as part of this integration.

The acceptance worker uses only isolated output/baseline directories under
`forcing/work/nrt-gfs-cycle-validation/job_4520199/`. It deliberately selects
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

The installed crontab already invokes `bin/update_nwm_forcing.py` at 02, 08, 14
and 20 UTC. Those commands need no replacement: the coordinator reads the new
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
gating. The pre-acceptance suite passes 248 tests. Operational throughput and the
complete real-data replacement/PRISM chain still require job 4520199's result.
