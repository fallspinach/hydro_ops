# Yampa domain

The reporting region is the union of four USGS WBD HU8 polygons:

| HU8 | Name |
| --- | --- |
| 14050001 | Upper Yampa |
| 14050002 | Lower Yampa |
| 14050003 | Little Snake |
| 14050004 | Muddy |

The supporting region adds one **20 km outward buffer around the dissolved
union**, not an additional buffer at each subsequent processing step.
`bin/build_yampa_domain.py` reads the preserved official WBD HU2-14 geodatabase
archive from UCRB, verifies its recorded SHA-256 and copies it into Yampa's
source directory. Selection requires exactly one feature for each requested
code and matching names. Original attributes, geometry repairs and checksums
are retained in the manifest.

Buffering uses UCRB's metre-based local azimuthal-equidistant CRS (39 N, 109 W),
not geographic degrees. It is a projected rather than exact geodesic buffer.
The shared mask builder derives separate model and forcing masks on one aligned
CONUS grid window. Model activity is buffered boundary intersected with the
NLDAS2-compatible XLAND mask; forcing coverage intersects the CONUS static
envelope and retains inactive-but-valid cells. Active cells must all have
forcing coverage. The mask is not a channel-network deletion rule and does not
guarantee perfect routing closure.

Products under `nwm/static/domains/yampa/`:

- `boundaries/sources/WBD_14_HU2_GDB.zip`: checksum-verified original archive.
- `boundaries/yampa_boundaries.gpkg`: `wbd_hu8_selected`, `yampa_reporting`,
  `yampa_buffer_20km` layers.
- `boundaries/yampa_domain.png`: labeled HU8 union/buffer and mask map.
- `masks/yampa_masks.nc` and `.json`: aligned grid masks and diagnostics.
- `manifest.json`: provenance and technical acceptance, separate from approval.

Boundary/mask job **5038577** was submitted October 27, 2026 using
`slurm/build_yampa_domain.sh`: 16 CPUs, 20 GB scratch, two-hour limit on ordinary
shared resources. Seven focused tests, Ruff and shell syntax checks passed.
The builder refuses an existing target directory and retains failure evidence.
No existing domains are changed.

Job 5038577 passed in seven seconds. The aligned grid is **227 × 239**;
34,698 cells are active for the model and 34,715 are retained for forcing
(17 extra inactive-but-valid cells). Active cells without forcing: **zero**.
The reporting union covers approximately 21,384 km²; buffered support covers
37,015 km² (equal-area calculations). Technical acceptance passed and the user
subsequently approved the map.

## Approved forcing extraction from UCRB

The user subsequently approved the map and authorized forcing extraction. The
manifest records boundary/mask approval; model readiness remains untested.
Read-only containment checks found zero reporting/buffer area outside UCRB,
zero boundary/model/forcing mask cells outside the corresponding UCRB masks,
and complete containment of the rectangular grid window.

Job **5042206** runs `slurm/backfill_yampa_forcing.sh` with an explicit
`afterok:5038572` dependency on the UCRB backfill. It also requires that job's
specific final report to pass with zero failures and all files accounted for;
all source files in the parent campaign plan must exist. UCRB failure blocks
execution rather than silently extracting a partial archive.

`bin/backfill_yampa_forcing.py` extracts retro/NRT hourly, daily and monthly
products to `forcing/outputs/yampa/<stream>/<resolution>/`, preserving source
filenames and directory conventions. Monthly files retain year-only folders.
The job requests 64 CPUs, 16 workers, 120 GB scratch and 24 hours on ordinary
shared resources. It does not modify UCRB's running job or install cron hooks.

Each source must have a current accepted subset receipt. The shared nested
crop implementation handles parent offsets and target masks. Six initial
stream/resolution samples are compared exactly against independent CONUS crops
before bulk work proceeds. All files receive retained-value/missing-mask,
active-cell, metadata and transfer-checksum audits with atomic publication;
accepted unchanged destinations are skipped on retry. Interior hourly source
gaps fail closed. Summaries are copied where present, not newly generated.
Sample files published during the gate are counted as skipped by the subsequent
bulk pass. Reports are under `forcing/status/yampa/backfill-5042206/`.

Eight focused tests, Ruff and shell syntax checks passed before submission.
Submission and boundary approval do not imply completed forcing publication,
parameter extraction or model validation.

The backfill completed in 1:46:59 with 35,550 files accounted for, zero failures
and no interior hourly source gaps. Retro hourly collections cover January 1,
1979 through April 18, 2026; January 1, 1979 is partial. All six direct-CONUS
sample comparisons passed. This is an archive snapshot, not automatic cron
integration.

## Gated retro model campaign

The user authorized model runs after forcing extraction passed.
`bin/submit_yampa_production.py` verifies the approved geometry hashes, the
job-specific forcing acceptance and six-sample gate, and hourly forcing/receipt
coverage before submitting `slurm/run_yampa_production.sh` on ordinary shared
resources. It refuses an existing submission journal rather than duplicating a
chain. Submission does not imply model acceptance.

The dependency chain consists of parameter extraction, a warm-start/continuation
gate, and sequential annual jobs for 1979–2026. Simulation starts at
**1979-01-02 00 UTC** and ends at **2026-04-18 00 UTC**; forcing at the end
boundary is required. The initial paired land/hydro state is subset from the
preserved CONUS spin-up initialization. The gate compares a continuous run with
a warm-restarted continuation before releasing annual production. Each annual
job additionally requires its accepted predecessor and uses monthly checkpoints.

Model jobs request **32 MPI ranks**, 120 GB scratch and a 24-hour limit per year;
the gate has a two-hour limit. Parameter extraction uses one task with 32 CPUs
and a four-hour limit. Terrain/channel routing use 600/600-second timesteps,
lakes remain disabled, and fully coupled warm starts use `rst_typ=1`,
`RSTRT_SWC=0`, `GW_RESTART=1`, and `t0OutputFlag=0`.

The campaign preserves daily-resolution LDASOUT, both hourly- and daily-resolution
CHRTOUT, calendar-day stacking of hourly output, and monthly paired restart
checkpoints. Paths are `nwm/{runs,outputs,restarts}/yampa/retro/production_v1/`;
submission and job-specific acceptance reports are under
`nwm/status/yampa/production_v1/`. No shared model executable is rebuilt and no
other domain campaign is changed. Scientific validation and NRT scheduling remain
separate from this technical retro launch.

Submission on October 28, 2026 created parameter job **5050816**, warm-start gate
**5050817**, and annual jobs **5050818–5050865** (1979–2026). All annual jobs use
`afterok` dependencies and the job-specific gate acceptance. The full submission
journal is `nwm/status/yampa/production_v1/submission.json`. Before submission,
the full suite passed **553 tests**, with two skipped; focused tests, Ruff,
shell syntax and `git diff --check` also passed. These are software checks, not
completed model execution or scientific validation.

The retro chain subsequently completed all 48 annual acceptance intervals,
continuously from January 2, 1979 through April 18, 2026 at 00 UTC. The user
then authorized [NRT continuation](subdomain_nrt_catchup.md) from that endpoint,
with a forcing refresh and a new warm-start/continuation gate first.
