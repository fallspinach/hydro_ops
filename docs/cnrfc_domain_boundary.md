# CNRFC model-domain boundary

Domain geometry is stored under `nwm/static/domains/cnrfc/boundaries/`:

- `CNRFC_Basins.gpkg`: unchanged copy of the supplied 374 forecast-basin polygons.
- `official_cnrfc.gpkg`: unchanged copy of the existing NOAA official CNRFC boundary.
- `cnrfc_domain_boundaries.gpkg`: derived WGS84 (EPSG:4326) layers.
- `manifest.json`: source paths, SHA-256 hashes, CRS, repair counts, areas and checks.
- `cnrfc_union_buffer_20km.png`: full-domain and southern-extension preview.

Derived layers:

| Layer | Purpose |
| --- | --- |
| `official_cnrfc` | Official region in the common output CRS |
| `forecast_basins_dissolved` | Union of all supplied forecast basins |
| `basin_additions` | Forecast-basin area outside the official boundary |
| `cnrfc_union` | Official boundary plus all forecast basins; reporting/presentation region |
| `cnrfc_union_buffer_20km` | Union with an outward 20-km buffer; supporting model/forcing domain |

The union includes **all** forecast-basin additions, not only the area in Mexico.
There is no political-border clipping, boundary simplification or component removal.
Input CRS differences (official NAD83, forecast basins WGS84) are handled explicitly.
Invalid polygon geometry, if encountered, is made valid before dissolution; counts
are recorded in the manifest.

Buffering uses a WGS84 azimuthal-equidistant projection centered at 38°N, 119°W
and a 20,000-m distance, with 64 segments per quadrant. It is a local projected
metric buffer, not an exact geodesic buffer or a degree-based approximation.
Sampled projection scale distortion is recorded for transparency. Areas in the
manifest are projected areas, not an equal-area/geodesic area calculation.

Reproduce into a **new** output directory:

```bash
/home/mpan/local/miniforge3/bin/python bin/build_cnrfc_domain_boundary.py \
  --output /path/to/new/cnrfc-boundaries
```

This operation only prepares geometry. It does not alter the official CNRFC mask
used for Stage-IV corrections, production forcing, NWM run masks, routing or
model parameters. Subsequent domain subsetting must still check drainage closure
and distinguish fully supported watersheds from edge-affected reaches; buffering
alone does not establish hydrologic completeness.

## Shared model/forcing grid and masks

`nwm/static/domains/cnrfc/masks/cnrfc_masks.nc` contains three aligned binary
rasters plus latitude/longitude. Its companion `cnrfc_masks.json` records counts
and inclusive source-grid windows.

| Mask | Definition | Cells |
| --- | --- | ---: |
| `boundary_mask` | Cell centers intersect the buffered polygon | 721,977 |
| `model_mask` | Boundary AND `wrfinput_CONUS_NLDAS2.nc` XLAND == 1 | 674,999 |
| `forcing_mask` | Boundary AND CONUS v4 static envelope `keep` | 685,632 |

There are **10,633 additional forcing cells** and **zero active cells outside
forcing coverage**. This verifies static coverage eligibility, not completeness
of every historical/hourly forcing file. Per-file active-cell audits remain required.
Inactive/water forcing is intentionally preserved where the parent envelope allows
it. No existing model-inactive cell is activated.

Both products have the **same 1,332 rows × 850 columns**, native 1-km grid,
coordinates and index order. Only their masks differ. The shared inclusive CONUS
window is `y=1263:2594, x=77:926`. The aligned 250-m routing window is
`y=5052:10379, x=308:3707` (5,328 × 3,400). No second 20-km buffer or additional
padding is applied. Rectangular corners outside the polygon remain present in the
arrays but masked/inactive. Membership uses cell centers; the geographic buffer
already supplies the requested margin.

Rebuild with `bin/build_nwm_domain_masks.py` using base Miniforge Python and
`PYTHONPATH=src`; existing mask output is protected against overwrite. Input grid
coordinates, the CONUS envelope checksum, output mask checksums and model/forcing
containment are checked.

### Parameter subset planning

```bash
conda activate hydro-ops
python bin/subset_nwm_domain.py \
  --domain-dir nwm/static/operational/nwm.v3.1.6/domain \
  --domain-masks nwm/static/domains/cnrfc/masks/cnrfc_masks.nc \
  --output-dir nwm/static/domains/cnrfc/parameters
```

The default is a dry run. Full parameter extraction subsequently passed as job
4585396. `--execute` performs extraction, aligns the
routing grid, and deactivates land cells outside `model_mask` in the derived
wrfinput without changing the source. Do not use the land mask to delete channels
or water pixels. Existing topology/completeness flags describe rectangular
clipping, **not** loss of contributing land at the polygon edge. The manifest marks
polygon activity auditing as required; a full polygon-aware drainage audit and
model smoke test were originally identified as further validation steps.

Operational decision: imperfect drainage closure is acceptable for this domain.
The **unbuffered union of the official CNRFC boundary and all forecast basins**
(`cnrfc_union` in `cnrfc_domain_boundaries.gpkg`) is the reporting/presentation
region, including the forecast-basin extensions into Mexico. Only the additional
20-km buffer is supporting area outside the reporting region. Retain boundary diagnostics
and require structural consistency, but do not block extraction on perfect
watershed completeness. The buffer is a practical safeguard, not proof that every
reported reach is free of boundary influence. A model smoke test still follows
parameter extraction. `slurm/extract_cnrfc_parameters.sh` stages extraction on
reserved node scratch, verifies grid/mask agreement and file checksums, then
publishes `nwm/static/domains/cnrfc/parameters/` without replacing existing assets.
It keeps lakes and diversions disabled, as in the existing subset workflow.

Extraction submitted as **4585396** with 32 allocated CPUs (~64 GB under the
cluster's 2-GB/CPU allocation), 120 GB reserved scratch and a 12-hour limit.
Report: `nwm/status/cnrfc/parameter-extraction/job_4585396/acceptance.json`.
The acceptance report is **passed**, with a 1332×850 model grid and 5328×3400
routing grid. This is parameter/grid/checksum acceptance, **not** a model smoke test.

Reporting-region clarification: job 4585396 was submitted before this wording
correction and its batch snapshot may label the official boundary alone as the
reporting region in descriptive manifest fields. That label is superseded by the
`cnrfc_union` definition above; it is not used to extract or mask parameters.
The buffered geometry, both masks, crop windows and parameter values are unchanged;
no job restart is needed. The checked-in extraction script now records the union
and layer for future extractions; existing artifact metadata is not rewritten here.

### Forcing subset

```bash
python bin/subset_nwm_forcing.py \
  forcing/outputs/conus/retro/hourly/1981/01/19810101.LDASIN_DOMAIN1 \
  forcing/outputs/cnrfc/retro/hourly/1981/01/19810101.LDASIN_DOMAIN1 \
  --domain-masks nwm/static/domains/cnrfc/masks/cnrfc_masks.nc
```

This is a command example, not a claim that the CNRFC archive has been produced.
The tool crops every spatial variable to the exact same window, masks the eight
hourly forcing fields (or daily/monthly summary fields) outside `forcing_mask`,
retains time coordinates and bounds, and compares all
retained values against the source. It rejects missing active-cell values without
attempting interpolation or gap filling. Outputs include both masks and a separate
`.subset.json` audit; inherited CONUS mask/audit attributes are renamed with a
`parent_` prefix so they do not masquerade as subset audits. Failed candidates are
not published. The caller must give a new output path.

Six focused tests passed (mask relationships, bad coverage/alignment, grid-window
selection and real NCO/NetCDF small-grid extraction). No full-size forcing pilot
is implied by those tests. Full parameter extraction passed separately as above.

### Hourly/daily/monthly forcing pilot

`slurm/test_cnrfc_forcing_subset.sh` tests retro 1981-01-15 and 2003-01-15,
plus NRT 2026-04-12 and 2026-09-24. It also subsets retro daily 1981-01-15 and
monthly January 1981, and NRT daily 2026-05-01 and monthly May 2026 (eight files
total). Summary files retain mean RAINRATE, WIND_SPEED, temperature extrema,
units, cell methods, and original time bounds; no temporal reduction is repeated.
Parent aggregation signatures are renamed rather than presented as subset audits.
Daily outputs use `daily/YYYY/MM/YYYYMMDD.LDASIN_DOMAIN1.daily`; monthly outputs
use `monthly/YYYY/YYYYMM.LDASIN_DOMAIN1.monthly` under the CNRFC stream root.
It requests eight CPUs, 120 GB node scratch,
and four hours, processing one file at a time. Subsetting and full retained-value
checks run on scratch; checksum-verified outputs are published under
`forcing/outputs/cnrfc/{stream}/hourly/YYYY/MM/` without overwriting existing files.
Source identities must remain unchanged during extraction and publication.
Per-file `.subset.json` audits and
`forcing/status/cnrfc/subset-pilot-JOBID/acceptance.json` record coverage and timing.
This is a forcing compatibility/performance test, not a CNRFC model simulation;
submission alone does not establish acceptance.

Pilot job **4638428 passed** all eight files in 4m12s (eight allocated CPUs,
sequential processing, peak RSS about 1.8 GB). Hourly collections took 54–58s each;
daily/monthly summaries took 6–7s. All retained values matched exactly and all
674,999 active cells had valid values. This is not a model-run acceptance.

### Parallel archive backfill

`slurm/backfill_cnrfc_forcing.sh` allocates 64 CPUs on one node, 120 GB scratch,
and 48 hours, running 16 independent file workers. It snapshots all existing
CONUS retro/NRT hourly, daily, and monthly file identities; baseline is excluded.
The concurrency leaves memory/I/O headroom rather than assuming 64 simultaneous
readers will scale linearly. All full subset checks remain enabled.

`bin/backfill_cnrfc_forcing.py` records the plan, incremental `status.json`, and
per-file `results.jsonl` under `forcing/status/cnrfc/backfill-JOBID/`. Scratch
temporaries are removed after each task. Permanent publication is checksum checked
and atomic; only known audited outputs can be replaced. Unchanged source/mask
identities plus an output checksum match permit skipping on a rerun. Pilot outputs
are rebuilt once to add the mask checksum. Source changes during the snapshot or
processing fail that task safely; rerun against the new source snapshot. Failures
are collected without discarding other successful files and make the job fail.
Files created after discovery require another campaign. This is a one-time
backfill, not yet an automatic CNRFC refresh attached to the CONUS cron cycle.

### First model smoke test

Job **4638500**, submitted via `slurm/test_cnrfc_model.sh`, tests a cold start
from 1979-01-02 00 UTC through 1979-01-04 00 UTC (48 hours). It reads the CNRFC
retro hourly collections directly from year/month directories, including the
terminal midnight endpoint; the incomplete 1979-01-01 is not used. Resources:
32 MPI ranks, 120 GB scratch, four-hour limit. Routing remains enabled at
600/600-second terrain/channel timesteps, precipitation partition option 1,
lakes/diversions disabled, and t0 output disabled.

The runner verifies original parameter checksums, model-mask agreement, forcing
grid alignment, time coverage, and all eight fields on active cells before launch.
Daily LDASOUT and hourly CHRTOUT are audited after model completion, along with
the terminal restart. Hourly channel files are staged on scratch and collected
into calendar-day files for permanent publication using the production publisher.
Reports and accepted test outputs are under
`nwm/outputs/cnrfc/retro/tests/coldstart_19790102_48h/job_4638500/`.
Submission is not acceptance; inspect `acceptance.json` for the result. This is a
structural/I/O test, not spin-up or independent hydrologic validation.

Cold-start job **4638500 passed**: model runtime 77.2s, total runner time 107.7s,
48 hourly channel records, two daily land records, and valid terminal land/routing
restart states on 32 ranks.

### Restart subsetting and warm-start pilot

See the [restart subsetting guide](nwm_restart_subsetting.md) for the mapping
rules, command example, warm-start checklist, failure handling and limitations.

`bin/subset_nwm_restart.py` crops Noah-MP states at 1 km (including staggered
dimensions), hydro land states at 1 km, and terrain states at 250 m. Reach-based
states are selected using parent RouteLink IDs in **subset RouteLink row order**.
For this coupled UDMP configuration, `z_gwsubbas` is also indexed by `links`, not
the smaller subset GWBUCKPARM catchment dimension. The local model source confirms
this in `module_HYDRO_io.F90` (`w_rst_crt_reach`/`read_rst_crt_reach_nc`) and
`module_mpp_ReachLS.F90` (global reach gather/scatter); it is not CHRTOUT's sorted
feature order. The utility rejects lake and basin-indexed restart states rather
than guessing their mapping. Nudging restart extraction is not implemented.

Every extracted raw state value is compared with the parent, using bounded row
blocks; original restart times, accumulation states, and history counters are
preserved. No `his_out_counts` reset or artificial reinitialization is performed.
Source restarts are read-only. Inherited global projection-origin metadata remains
parent metadata; use the subset parameter grid for geolocation, not that origin.

Warm-start job **4638507** (`slurm/test_cnrfc_warm_start.sh`) extracts the CONUS
production restart at 1979-02-01 00 UTC and runs through February 3 00 UTC with
32 MPI ranks and the same output checks as the cold-start test. Reports, extracted
initial states and accepted outputs go to
`nwm/outputs/cnrfc/retro/tests/warmstart_19790201_48h/job_4638507/`.
Job **4638507 passed**: 3m38s scheduler elapsed, 94.1s model runtime, 216.8s total
runner time including extraction/audits/publication; 48 hourly channel records,
two daily land records, and valid terminal restart states. Its `acceptance.json`
records the result. This does not constitute independent hydrologic validation.
This transfers spun-up CONUS state but does not guarantee identical subsequent
flows: subset boundary inflows and retained catchment areas differ, particularly
at clipped upstream boundaries. No additional upstream forcing is invented.

### Three-year, 64-rank production benchmark

**Recovery notice:** the shared hourly CHRTOUT publisher was subsequently found
to omit the channel variables' time dimension when collecting one-hour files.
The earlier publication acceptance is insufficient; see
[CHRTOUT archive recovery](chrtout_archive_recovery.md). Native daily CHRTOUT,
daily LDASOUT, and restart files remain distinct from that faulty hourly archive.

Campaign `production_1979_1981_v1` runs from 1979-01-02 00 UTC through
1982-01-01 00 UTC. Jobs **4638584 (1979)**, **4638586 (1980)**, and
**4638587 (1981)** form an `afterok` chain, each requesting 64 MPI ranks on one
node, 120 GB scratch, and 48 hours. Only one yearly simulation runs at a time.
The entry points are `slurm/run_cnrfc_production.sh --year YEAR` and
`bin/run_cnrfc_production.py`.

Initialization is an exact CNRFC subset of the already-prepared CONUS
`initialization-preserved-counter` pair timestamped 1979-01-02, not the February
test state or a cold start. That parent state was recycled from the completed
1981–1985 spin-up; its existing provenance is copied and no further counters,
dates, or accumulations are reset by CNRFC extraction.

Both `CHRTOUT_HOURLY=1` and `CHRTOUT_DAILY=1` are enabled; LDASOUT is daily only.
Daily reductions remain model-native, with per-variable methods and 00–00 UTC
bounds. Each monthly segment checks the first day's daily streamflow against the
mean of its 24 hourly endpoint records, in addition to coverage, metadata, state,
and restart checks. Hourly files are written on scratch and collected into
calendar-day files before permanent publication, using the validated production
publisher; there is no new direct daily-file writer in this benchmark.

Each month publishes a restart pair at the following 00 UTC month boundary.
Accepted months are skipped on a resubmission, using their saved restart pair.
A failure stops the current job and blocks dependent years. Publication errors
may leave partial monthly outputs and require inspection before retrying; this
runner does not automatically roll back a partially published month.
Forcing availability is checked per month, including the next midnight record;
it waits up to two hours for the concurrent subset backfill, then fails visibly.

Paths (all beneath `nwm/`):

```text
runs/cnrfc/retro/production_1979_1981_v1/initialization/
runs/cnrfc/retro/production_1979_1981_v1/YYYYMM/{status,accepted}.json
outputs/cnrfc/retro/production_1979_1981_v1/hourly/YYYY/MM/YYYYMMDD.CHRTOUT_DOMAIN1
outputs/cnrfc/retro/production_1979_1981_v1/daily/YYYY/MM/YYYYMMDD.CHRTOUT_DOMAIN1.daily
outputs/cnrfc/retro/production_1979_1981_v1/daily/YYYY/MM/YYYYMMDD.LDASOUT_DOMAIN1.daily
restarts/cnrfc/retro/production_1979_1981_v1/YYYY/MM/
```

Monthly reports contain model seconds and total segment elapsed time, enabling
an annual throughput estimate with realistic dual-channel-output I/O. No annual
performance claim is inferred from the earlier two-day smoke tests.
