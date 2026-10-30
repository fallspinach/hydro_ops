# CNRFC model-domain boundary

## Canonical expanded-grid promotion

The user approved replacing the original grid on October 29, 2026. See
[promotion and retirement](cnrfc_grid_promotion.md) for the runtime gate,
validation sequence and state report. After promotion, `cnrfc` denotes the
expanded grid; `cnrfc_hu18_v1` remains its version identifier, not an operational
directory alias. The construction/pilot sections below are historical evidence.

## Approved HU2 18 boundary/mask (not yet operational)

The user approved the geometry and masks following visual review on 2026-10-21.
`nwm/static/domains/cnrfc_hu18_v1/approval.json` pins the approved boundary and
mask SHA-256 values. Approval authorizes candidate parameter extraction and
isolated tests. Subsequent user approval authorizes the bounded 1979–1980
production pilot below, **not operational cutover or full historical reprocessing**.
The preparation and test entry points reject changed approved assets.

### Parameter and model pilot

- **4950136**: extract candidate CONUS gridded and topology-aware routing
  parameters, verify grid/mask alignment and network consistency, stage and
  checksum-copy to `nwm/static/domains/cnrfc_hu18_v1/parameters/` without
  overwriting existing data. Then subset four hourly collections for each
  period: retro January 2–5, 1979 and NRT April 12–15, 2026. Sample inputs go
  under `forcing/outputs/cnrfc_hu18_v1/tests/<stream>/hourly/YYYY/MM/`.
- **4950138**: 64-rank model acceptance, dependent on successful preparation
  and its passed JSON report. For each period, run a three-day cold-start
  integration, save daily checkpoints, then repeat the last day from the
  checkpoint with `rst_typ=1`, `RSTRT_SWC=0`, `GW_RESTART=1`.
  Compare eight terminal land/routing state fields against the uninterrupted
  endpoint (rtol 1e-5, atol 1e-6). Cold starts use option 0 appropriately.

Tests keep terrain routing at 600/600 seconds, precipitation partition option 1,
lakes off and t0 output off. They check all eight forcing fields on active cells,
daily LDASOUT, hourly/daily CHRTOUT, archived record preservation, daily bounds,
daily restart timestamps and finite state. They are engineering acceptance,
not spin-up convergence or calibration/scientific validation.

Preparation evidence is in
`nwm/status/cnrfc_hu18_v1/preparation/job-4950136/acceptance.json` and
`nwm/static/domains/cnrfc_hu18_v1/parameter_acceptance.json`.
Model results/restarts are isolated under
`nwm/outputs/cnrfc_hu18_v1/tests/job-4950138/`.
Both jobs completed and their acceptance reports passed. The continuous tests
took approximately 112 seconds each; the warm continuations took 47 seconds.
All eight compared restart fields matched within tolerance (six exactly,
channel flows differing only by approximately 1.5e-39).
Existing `cnrfc` operations, parameters and schedules remain unchanged.

### Expanded-domain 1979–1980 production pilot

`bin/run_cnrfc_hu18_pilot.py` stages the first production campaign under the
distinct domain `cnrfc_hu18_v1`. The ordered, success-dependent stages are:

1. `forcing`: subset existing CONUS retro hourly/daily/monthly products for
   1979–1980, plus the hourly January 1, 1981 boundary file. Require every
   hourly date from January 2, 1979 through January 1, 1981 before starting.
   January 1, 1979 is included if present, but not simulated because it is
   partial. Summaries are copied where available; this does not generate missing
   parent summaries. The plan records the precise discovered inventory.
2. `gate`: subset the preserved-counter CONUS spin-up initialization into the
   expanded parameters. Its provenance traces back to the January 1, 1986
   CONUS spin-up restart, re-dated to January 2, 1979 with diagnostic accumulators
   reset and physical state preserved. **No old CNRFC restart is used.** Run
   January 2–5 continuously from that state, repeat the last day from its saved
   restart, and compare terminal land/routing states. This also checks both
   channel output resolutions, daily land output, and archived hourly records.
3. `1979`, then `1980`: annual runs with monthly acceptance/checkpoint boundaries.
   Each requires the passed warm-start gate and pinned parameters/mask. The first
   year also verifies the initial restart pair's gate-recorded checksums.

Forcing uses `forcing/outputs/cnrfc_hu18_v1/retro/<resolution>/` with the existing
hourly/daily year/month and monthly year layout. Model products remain isolated:

- `nwm/outputs/cnrfc_hu18_v1/retro/hu18_pilot_v1/{hourly,daily}/YYYY/MM/`
- `nwm/restarts/cnrfc_hu18_v1/retro/hu18_pilot_v1/YYYY/MM/`
- `nwm/runs/cnrfc_hu18_v1/retro/hu18_pilot_v1/` (monthly acceptance and yearly completion)
- `nwm/status/cnrfc_hu18_v1/hu18_pilot_v1/job-<id>/` (forcing/gate evidence)

The backfill allocates 64 CPUs, runs 16 independent subset workers, and requests
120 GB scratch. Model jobs use 64 MPI ranks and 120 GB scratch, `rst_typ=1`,
600/600 routing, daily LDASOUT, hourly plus daily CHRTOUT, and monthly paired
restarts. Native hourly channel records are archived into calendar-day files;
daily reductions retain 00–00 UTC interval bounds. SLURM entry points are
`slurm/backfill_cnrfc_hu18_pilot.sh` and `slurm/run_cnrfc_hu18_pilot.sh`.
Any failed predecessor blocks its successors. Passing this engineering pilot
does not certify calibration or spin-up convergence, promote outputs, change
cron configuration, or authorize automatic continuation beyond 1980.

Submitted October 21, 2026: forcing **4956454**, warm-start gate **4956455**,
1979 **4956456**, 1980 **4956457**, each dependent on successful completion of
the preceding stage. Submission evidence is
`nwm/status/cnrfc_hu18_v1/hu18_pilot_v1/submission.json`. All 731 required parent
hourly date files existed at submission; content checks occur in the compute
jobs. The full Python suite passed (523 passed, 2 skipped). This records
submission and software checks, not completed production acceptance.

### Authorized retrospective continuation from 1981

October 27 extension: March 3–4 CONUS retro repairs passed and coverage is now
continuous through April 18. The original continuation plan remains frozen for
the existing chain. Its still-pending 2026 model job **4972796** was held and
replaced, then cancelled (no model output was removed). New plan
`config/cnrfc_hu18_retro_extension_2026.json` extends the 2026 model endpoint to
**April 18, 2026, 00 UTC**, using that date's forcing for the boundary hour.
Forcing catch-up **5037300** subsets available 2026 products and skips accepted
unchanged subsets. Replacement model **5037301** requires successful completion
of both catch-up and 2025 model **4972794**. Both request 64 CPUs and 120 GB
scratch; time limits are two and six hours respectively. Existing output modes,
monthly restart pairs, option-1 warm starts, publication gates, and campaign
paths remain unchanged. This extends only `cnrfc_hu18_v1` retro, not operational
CNRFC NRT. These are submissions, not publication/acceptance results. The
original submission journal records the superseded job; use this replacement
lineage when tracking 2026. The earlier March 2 endpoint below describes the
original submission.

The 1979–1980 pilot completed: 1,485 forcing products passed subsetting audits,
the CONUS-spin-up warm-start test matched all eight terminal fields exactly,
and all 24 monthly model acceptance checks passed. Annual wall times were
3h16m and 3h18m on 64 ranks, with daily LDASOUT, hourly/daily CHRTOUT and
monthly paired restarts. The user subsequently authorized continuation from
1981 through the continuous available CONUS retro record.

`config/cnrfc_hu18_retro_continuation.json` freezes the continuation interval:
January 1, 1981 to **March 2, 2026, 00 UTC** (exclusive model end). CONUS hourly
files exist after this date, through April 18, but **March 3–4 are absent**;
do not bridge that gap with NRT data or silently skip model days. Closing that
gap and extending the endpoint requires a separate follow-up.

`bin/submit_cnrfc_hu18_continuation.py` verifies parent filename coverage through
the final boundary hour, pilot acceptance and the initial restart pair, then
submits two dependency chains: serialized yearly forcing backfills and
sequential yearly model runs. Each model job requires its year's backfill and
the preceding model year to succeed. A failure blocks downstream jobs rather
than bypassing acceptance. Each backfill includes the next-year boundary day;
its overlap is safely skipped using audited source/output identities.
Available summaries are subset too; only complete calendar months within each
simulation interval are selected for monthly summaries.

The existing `hu18_pilot_v1` campaign directory name is retained deliberately
to continue directly from the accepted state without moving files. It now holds
the longer isolated expanded-domain campaign, not only the two-year pilot.
Nothing is promoted into the operational `cnrfc` domain, and cron is unchanged.
Do not edit the frozen plan or pinned parameters/masks while this chain is live.

Forcing jobs retain 64 allocated CPUs/16 workers, model jobs 64 MPI ranks;
at most one of each chain runs concurrently (128 CPUs combined), plus unrelated
operations. Each requests 120 GB scratch. Yearly wall-time limits remain
24 hours for subsetting and 48 hours for modeling. The final 2026 model job
ends at the frozen partial-year endpoint. Submission commands and IDs are saved
incrementally in `nwm/status/cnrfc_hu18_v1/retro-continuation-1981-2026/submission.json`;
the submitter refuses duplicate journals, including uncertain partial submissions.

Submitted October 22, 2026: **92 jobs** (46 yearly forcing jobs and 46 model
jobs), IDs **4972705–4972796**. The first forcing job **4972705** started with
64 allocated CPUs, 125 GiB allocated memory and 120,000 MB requested scratch;
the first model job **4972706** waits on its success. The final pair is
**4972795/4972796** for the partial 2026 interval. Full software regression:
525 passed, 2 skipped. Submission is not production acceptance; inspect each
job's report and monthly/annual model acceptance files as the chain advances.

### Boundary and mask build evidence

The candidate under `nwm/static/domains/cnrfc_hu18_v1/` was built successfully by
job **4949930** in 23 seconds. It implements the requested exact definition:

- Reporting region: **official CNRFC union USGS WBD HU2 18**.
- Supporting model/forcing region: the reporting region buffered outward by
  **20,000 m**, once, using the same local AEQD projection as the original domain.
- Forecast-basin polygons are not an additional union input for this candidate.

The official USGS `WBD_18_HU2_GDB.zip` is retained under `boundaries/sources/`,
along with its extracted geodatabase and copied official CNRFC boundary. The
root `manifest.json` records URL, server Last-Modified/ETag, SHA-256 checksums,
selected feature attributes (`huc2=18`, `California Region`, `CA,MX,NV,OR`),
geometry validation, mask validation, and comparisons with the operational domain.
Unlike the preliminary web-service geometry, the downloaded source HU2 geometry
was valid without repair. The archive server timestamp was January 8, 2025;
the feature load date was August 7, 2024. These are source provenance dates,
not the candidate's build date.

Candidate products:

- `boundaries/cnrfc_hu18_boundaries.gpkg`: source, union, buffered union,
  reporting-area additions/omissions, and old-buffer comparison layers.
- `boundaries/cnrfc_hu18_comparison.png`: whole-domain and southern-extension map.
- `masks/cnrfc_hu18_masks.nc`: aligned `boundary_mask`, `model_mask`,
  `forcing_mask`, latitude and longitude.
- `masks/cnrfc_hu18_masks.json`: grid window, cell counts and coverage checks.

| Quantity | Existing CNRFC | HU2 18 candidate |
| --- | ---: | ---: |
| Shared 1-km grid (rows × columns) | 1,332 × 850 | 1,343 × 857 |
| Buffered boundary cells | 721,977 | 744,289 |
| Active model cells | 674,999 | 681,063 |
| Forcing cells | 685,632 | 691,832 |
| Active model cells without forcing-envelope coverage | 0 | 0 |

The candidate window is inclusive CONUS `y=1252:2594, x=70:926`;
its aligned 250-m routing window is `y=5008:10379, x=280:3707`.
The mask rules remain unchanged: boundary AND parent XLAND==1 for the model;
boundary AND the existing CONUS static envelope for forcing. No parent-inactive
cell is activated. There are 10,769 additional forcing cells beyond the model mask.
This establishes static coverage eligibility, not per-hour forcing validity.

The exact replacement is not strictly a superset of the old forecast-basin union:
approximately 50.58 km² of old reporting region is omitted. At buffered mask
level, 6,096 active cells are added and 32 removed (net +6,064); these differences
are explicitly preserved in the manifest. The expanded southern buffer reaches
approximately 31.953°N. Equal-area reporting-region area is 667,781 km² and
buffered area is 783,470 km². Local-projection buffer scale distortion sampled
over the bounding box is at most about 0.30%; the buffer is not exactly geodesic.

Reproduce into a **new, nonexistent** candidate directory:

```bash
source bin/project_environment.sh
sbatch slurm/build_cnrfc_hu18_domain.sh --output nwm/static/domains/cnrfc_hu18_v2
```

The job runs the download, geometry operations and parent-grid mask calculation
on a compute node using base Miniforge's geospatial environment. The builder
rejects existing output roots. Two geometry/security tests and six existing mask
tests passed. No operational parameters, forcing subsets, restart files,
simulations, schedules, or Stage-IV correction boundaries were modified.
Parameter extraction and model/forcing validation followed as recorded above.

## Existing operational domain

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

Historical initial-campaign paths (all beneath `nwm/`; these are not the
current published recovery products):

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

The corrected recovery campaign's validated outputs and restarts are promoted
to the canonical paths in [project layout](project_layout.md#cnrfc-model-publication).
Run records remain under `nwm/runs/cnrfc/retro/recovery_record_stack_v1/`.
Consult `nwm/status/cnrfc/retro-promotion/promotion.json` for completion of the
promotion, and [archive recovery](chrtout_archive_recovery.md) for its audit.

## NRT warm-start continuation

The runner supports a separate NRT stream via `--stream nrt`, explicit
`--start-date`/`--end-date` (00 UTC, within the specified `--year`), and
`--initial-restart-dir`. The initial pair is read without modifying its dates,
counters or state. The first continuation starts from the accepted retro pair
at 2026-03-02 00 UTC in `nwm/restarts/cnrfc/retro/2026/03/`, using
`forcing/outputs/cnrfc/nrt/hourly/`. Forcing at the end timestamp is required.

Run `python bin/submit_cnrfc_nrt_test.py` once to submit the isolated two-day
March 2–4 test: 64 MPI ranks, 120 GB requested scratch, two-hour time limit,
ordinary shared-128 resources (no NRTRES). The submission record prevents
accidental duplicate submission. This test does **not** automatically launch
the remaining NRT period.

Test outputs and terminal restart pairs use
`nwm/{outputs,restarts}/cnrfc/nrt/tests/nrt_warmstart_test_v1/`; run reports use
`nwm/runs/cnrfc/nrt/nrt_warmstart_test_v1/20260302-20260304/`.
The `nrt_continuation_v1` campaign instead selects canonical NRT publication
roots, but should not be used for the full continuation until this test passes.
All NRT campaigns share a stream-level model lock; retro publication is untouched.

Acceptance retains the established model completion, active land-state,
restart, output coverage, full-variable hourly archive verification and daily
streamflow numerical checks. NRT additionally checks subset receipt identities
and rejects publication if forcing file identities change during the model run.
Outputs remain daily LDASOUT plus hourly and daily CHRTOUT, with calendar-day
hourly collections and 00–00 UTC daily bounds. Monthly segments publish 00 UTC
checkpoints, including a terminal checkpoint for a partial month. Routing uses
600/600-second steps, precipitation partition option 1, lakes off and t0 output
off, matching the validated retro setup.

This is a bounded continuation runner, not yet a rolling NRT rewind planner.
Saved accepted segments are skipped; revising an already accepted simulation
requires a separately coordinated replay rather than treating it as a new run.

### Initial NRT catch-up — October 10, 2026

Warm-start job **4799797** passed March 2–4 in 95 seconds (76 seconds model
execution) on 64 MPI ranks: 48 verified hourly channel records, two daily
LDASOUT and two daily CHRTOUT files, and a validated terminal restart pair.

The initial canonical catch-up is submitted with:

```bash
source bin/project_environment.sh
"$HYDRO_OPS_PYTHON" bin/submit_cnrfc_nrt_catchup.py --end-date 2026-10-10 --submit
```

It starts from the **March 2 retro restart**, regenerating the two test days
into canonical NRT publication rather than mixing test and production products.
The fixed endpoint is October 10 at 00 UTC (222 simulated days); forcing was
available through October 10 at 07 UTC at planning time. Later same-day hours
are deferred to maintain midnight operation boundaries. The submission requires
warm-start acceptance, an empty canonical NRT destination, no queued/running
CNRFC jobs, continuous required forcing times/variables and current passed
subset receipts. Omit `--submit` for a read-only data preflight; a lock file and
run directory may be created. A durable submission manifest prevents duplicates:
`nwm/runs/cnrfc/nrt/nrt_continuation_v1/initial-catchup-submission.json`.

Resources are 64 MPI ranks, 120 GB requested scratch and a six-hour limit on
shared-128, without NRTRES. Job **4807655** was submitted after the preflight
passed. The runner processes months sequentially, writes
paired restart checkpoints at each month boundary and at the final endpoint,
and retains per-segment acceptance reports. It uses canonical
`nwm/outputs/cnrfc/nrt/{hourly,daily}/YYYY/MM/` and
`nwm/restarts/cnrfc/nrt/YYYY/MM/`. Routine rolling model updates are **not**
scheduled by this submission; they will be configured after catch-up acceptance.
If concurrent forcing updates change a file during a segment, publication fails
safely and that segment needs a retry from the preceding accepted checkpoint.

The [daily NRT model cycle](cnrfc_nrt_daily_operations.md) is now enabled after
restart acceptance and successful refresh 4821570: 11-day rewind planning,
daily restart pairs, pinned forcing inputs, `rst_typ=1`, staged replay publication
and a successful-dependency hook after the daily forcing cycle. It does not
require a separate cron entry and does not run after six-hourly forcing cycles.
