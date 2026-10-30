# UCRB domain candidate

The reporting region is **USGS WBD HU2 14, Upper Colorado Region**, without
unioning it with any CNRFC boundary. Model/forcing support adds a single
**20 km outward buffer**. The official source is the
[USGS WBD collection](https://data.usgs.gov/datacatalog/data/USGS%3A0101bc32-916e-481d-8654-db7f8509fd0c),
using the staged `WBD_14_HU2_GDB.zip` geodatabase.

`bin/build_ucrb_domain.py` preserves the downloaded archive and its SHA-256,
ETag and Last-Modified metadata. It requires exactly one `WBDHU2` record with
HUC2 14 and the name Upper Colorado Region, records source attributes and
geometry repairs, and buffers in a local metre-based azimuthal-equidistant
projection centered at 39 N, 109 W. The buffer is projected, not an exact
geodesic buffer; sampled projection scale is reported. HU4 boundaries are
included only as map context, not as a different domain definition.

Products under `nwm/static/domains/ucrb/`:

- `boundaries/sources/WBD_14_HU2_GDB.zip`: preserved original source.
- `boundaries/ucrb_boundaries.gpkg`: source HU2, `ucrb_reporting`,
  `ucrb_buffer_20km`, and HU4 context layers.
- `boundaries/ucrb_domain.png`: boundary/buffer and model/forcing mask map.
- `masks/ucrb_masks.nc` and `.json`: aligned masks and grid/coverage diagnostics.
- `manifest.json`: provenance, technical acceptance, and review state.

The shared mask builder rasterizes grid-cell centers within the buffered
boundary. The model mask intersects the existing NLDAS2-compatible CONUS
XLAND==1 mask; forcing intersects the CONUS static envelope and retains
inactive-but-valid cells. Grid windows match exactly and every active model
cell must have forcing coverage. These masks do not imply perfect routing
closure or channel-network deletion.

Job **5037637** was submitted October 27, 2026 with 16 CPUs, 20 GB requested
scratch and a two-hour limit through `slurm/build_ucrb_domain.sh`. Geodatabase
extraction and mask processing run on the compute node. Seven focused tests,
Ruff and shell syntax checks passed before submission.

Job 5037637 completed successfully in 15 seconds. The manifest reports passed
geometry/mask checks: 908 × 641 grid, 335,663 active model cells, 336,607 forcing
cells (944 additional inactive-but-valid cells), and zero active cells without
forcing. Native source geometry required no repair. The reporting area is
293,569 km² and buffered support area approximately 356,487 km² in EPSG:5070.
The sampled maximum projection scale is 1.00146. Review approval remains pending.

Technical `status: passed` is separate from `approved_for_production: false`.
The user approved the boundary/masks after map review on October 27; the
manifest now records `approved_for_production: true` with approval scoped to
boundary/masks and forcing extraction, not model validation. The boundary
builder itself launches no parameter extraction, backfill, model run or cron
integration; the separately authorized forcing backfill is described below.
The target domain directory must not already exist; failed builds retain
evidence and require inspection before any retry. Existing domains are untouched.

After approval, extract parameters and forcing directly from CONUS, retaining
the reporting/support distinction and common grid. Warm-start/model validation
must precede an operational production chain.

## Forcing backfill

Following boundary approval, `bin/backfill_ucrb_forcing.py` extracts available
**retro and NRT hourly, daily and monthly** products directly from CONUS into
`forcing/outputs/ucrb/<stream>/<resolution>/`, preserving parent filenames and
directory conventions (monthly files use year directories). This is cropping,
not remapping, new gap filling, or temporal reduction. Missing parent summaries
are not generated. The partial first retro day and latest partial NRT day retain
their actual source records.

The backfill requires unchanged approved boundary/mask hashes and no interior
hourly filename gaps. Six initial samples, one per stream/resolution, must pass
the existing subset audits before bulk processing. Each subset verifies grid
coordinates, retained values/missing masks, required active-cell completeness,
time/bounds metadata, transfer checksum and source stability. Source replacement
races have bounded retries; unrecognized existing outputs are not overwritten.
An exclusive destination lock prevents duplicate writers. Unchanged accepted
outputs are skipped on reruns, allowing later catch-up without rebuilding all
files. Snapshot completion is distinct from staying current with subsequent
CONUS updates; this does not install a UCRB cron hook.

`slurm/backfill_ucrb_forcing.sh` requests 64 CPUs, 16 workers, 120 GB scratch
and a 48-hour limit on ordinary shared resources. Reports are in
`forcing/status/ucrb/backfill-JOB/`: `plan.json`, `sample_acceptance.json`,
`results.jsonl`, and `status.json`. Sample publications are counted separately;
the bulk pass reports those same unchanged files as skipped. No model parameter
extraction or simulation is authorized by this forcing backfill.

Backfill **5038572** was submitted October 27, 2026. Eight focused tests, Ruff
and shell syntax checks passed. Submission is not publication acceptance;
inspect the job-specific sample and final status reports for results.

## Authorized retro model campaign

The user authorized retro simulations with **64 MPI ranks**. The isolated
`production_v1` campaign spans January 2, 1979 through April 18, 2026 at 00 UTC,
with that final date providing the boundary forcing hour. January 1, 1979 is
not simulated because its forcing is incomplete.

`bin/submit_ucrb_production.py --forcing-job 5038572` submits parameter
extraction, a warm-start gate, and sequential annual model jobs. Submission is
journaled in `nwm/status/ucrb/production_v1/submission.json`; duplicate submission
is refused. Early years with all hourly files and receipts already available
can proceed concurrently with forcing extraction. The first incomplete year
explicitly depends on the successful full forcing backfill; later years inherit
that dependency through their predecessors. Model runtime checks still validate
receipt identities, active forcing values and each monthly input interval.

Parameter extraction reuses the audited CONUS subset implementation with UCRB
mask/boundary names and the unbuffered `ucrb_reporting` layer. It verifies grid
alignment, model XLAND, routing topology and copy checksums before publishing
`nwm/static/domains/ucrb/parameters/` and `parameter_acceptance.json`. Drainage
completeness diagnostics are retained; lakes remain disabled.

The gate subsets the preserved CONUS spin-up initialization into UCRB, runs
January 2–5 continuously, and compares eight land/routing state fields with a
January 4–5 restarted continuation (rtol 1e-5, atol 1e-6). It validates outputs
and active land states before authorizing production. Every annual run checks
the accepted gate, pinned parameters and initial restart hashes, plus the
previous year's acceptance and paired checkpoint. Failure blocks successors.

`slurm/run_ucrb_production.sh` requests 120 GB node scratch, ordinary shared
resources, and 64 ranks with one thread each for model work. Parameter extraction
uses one task with 64 allocated CPUs. Limits are four hours for extraction,
two hours for the gate and 24 hours per annual job. Throughput will be measured.
No NRTRES is requested and the shared executable is not rebuilt.

Outputs retain daily LDASOUT, hourly/daily CHRTOUT, calendar-day hourly record
stacking, and monthly paired restart checkpoints. Settings remain `rst_typ=1`,
`RSTRT_SWC=0`, `GW_RESTART=1`, terrain/channel time steps 600/600 seconds, and
`PCP_PARTITION_OPTION=1`. Paths are
`nwm/{outputs,restarts,runs}/ucrb/retro/production_v1/`; gate outputs are under
`nwm/outputs/ucrb/tests/`. No NRT transition or cron model hook is activated.

The shared B120 runner and parameter helper now accept explicit UCRB arguments;
their existing B120 defaults, scientific settings and publication paths are
unchanged. Other active campaigns remain isolated.

Submitted October 27: parameters **5042256**, warm gate **5042257**, annual jobs
**5042258–5042305** (1979–2026). At submission, forcing coverage through the
2010 endpoint was available; **2011 job 5042290** additionally requires
`afterok:5038572`. All subsequent years inherit that gate. The regression suite
passed (538 passed, two skipped), with focused tests, Ruff and shell checks.
Submission is not parameter/model acceptance; inspect job-specific reports.
