# B120 NWM subdomain

## Definition and source

The reporting region is the union of **all 26 polygons** supplied in
`/cw3e/mead/projects/cwp206/nwm_v3/basins24/domain/watersheds_202410/B120Basins/B120Basins.shp`.
No filtering on attributes such as `isitB120` is applied. The model/forcing
support region adds a **20 km outward buffer**, once, around that union.

The input CRS is NAD83 California Teale Albers in US survey feet. Buffering
uses the same metre-based local AEQD CRS as CNRFC (38 N, 119 W, WGS84), not
native feet or geographic degrees. Source inspection found 10 invalid polygons,
with no null/empty features. Originals and sidecars are copied with SHA-256
checksums before `make_valid` repair and dissolution. Repairs are recorded in
the manifest. The projected buffer is not an exact geodesic construction.

## Products

All products are isolated under `nwm/static/domains/b120/`:

- `boundaries/sources/B120Basins.*`: preserved input shapefile and sidecars.
- `boundaries/b120_boundaries.gpkg`: `repaired_basins`, `b120_union` reporting
  region and `b120_buffer_20km` supporting region, in EPSG:4326.
- `boundaries/b120_domain.png`: basin/buffer and model/forcing mask overview.
- `masks/b120_masks.nc` and `.json`: aligned boundary, model and forcing masks.
- `parameters/`: gridded land/routing parameters, topology-aware network tables,
  copied `domain_masks.nc`, and drainage-completeness diagnostics.
- `manifest.json`: source provenance, geometry repairs, mask checksums and counts.
- `parameter_acceptance.json`: extraction checks, parameter hashes and network counts.

Model and forcing grids share the exact parent-grid window. The model mask is
the buffered boundary intersected with the NLDAS2-compatible parent XLAND==1;
the forcing mask intersects the same boundary with the CONUS static envelope.
Inactive-but-valid forcing cells remain retained. Every active model cell must
have forcing-envelope coverage. The aligned routing grid uses refinement factor 4.
No regridding or forcing gap filling occurs during this domain extraction.

The extractor verifies latitude/longitude, XLAND, routing dimensions and network
structural consistency. `subset_flow_validity.nc` retains complete-watershed and
boundary-affected diagnostics; neither the buffer nor extraction guarantees
perfect routing closure. No lake parameters are fabricated. A model run should
keep lakes disabled until suitable parameters are available.

## Build and acceptance

The boundary/mask stage passed on October 27: shared 1-km grid **764 × 319**,
CONUS inclusive indices `y=1653:2416, x=178:496`; aligned routing grid
**3056 × 1276**. There are 118,764 active model cells and 120,204 retained
forcing cells (1,440 additional inactive-but-valid cells), with **zero active
cells outside forcing coverage**. Parameter acceptance remains a separate gate.

`bin/prepare_b120_domain.py` builds geometry/masks with the base geospatial
environment, then parameters with the hydro-ops environment. Both stages run
on a compute node. Large parameter intermediates use job-local scratch;
checksum-verified copies are staged before publishing `parameters/`.
Existing domain roots/parameter directories are never overwritten.

Submitted **5030685** on October 27, 2026, via `slurm/prepare_b120_domain.sh`:
32 allocated CPUs, 120,000 MB requested scratch, four-hour time limit.
Log: `nwm/logs/b120-domain-5030685.out`. Submission is not acceptance; require
both JSON reports to say `passed`. The parameter report explicitly records
`model_smoke_tested: false`. Model/forcing runtime testing and operational
registration are separate next steps; existing CNRFC/CONUS operations are unchanged.

Reproduction requires a nonexistent B120 target directory:

```bash
source bin/project_environment.sh
sbatch slurm/prepare_b120_domain.sh /path/to/B120Basins.shp
```

## Forcing backfill from CNRFC

B120's reporting polygon, buffered boundary, rectangular grid window, model
cells and forcing cells are entirely contained in both CNRFC domains. The
initial backfill uses the established `forcing/outputs/cnrfc/` archive, not the
still-building `cnrfc_hu18_v1` archive. `bin/backfill_b120_forcing.py` copies
both retro and NRT streams, including hourly, daily and monthly products, into
`forcing/outputs/b120/<stream>/<resolution>/` using the parent filename/layout.
Monthly summaries retain their year-only directory convention.

Nested crops subtract the parent file's absolute CONUS grid-window offsets.
They verify coordinates and mask containment, replace inherited subset mask
variables with B120 masks, and retain parent subset provenance. All required
weather fields must remain complete on active cells; diagnostic missing values
remain untouched. Retained data, time coordinates, bounds and nonspatial
metadata are audited against the immediate CNRFC source. No remapping,
interpolation or new temporal reduction is performed.

Before the bulk run, six samples (each stream/resolution) must match direct
CONUS-to-B120 crops numerically and in missing-value masks. Then 16 workers
perform restartable per-file publication with source identity checks, accepted
parent receipts, mask checksums, transfer checksums and atomic replacement.
`slurm/backfill_b120_forcing.sh` requests 64 CPUs, 120 GB scratch and 48 hours.
Reports are under `forcing/status/b120/backfill-JOB/`; inspect
`chained_crop_acceptance.json`, `status.json` and `results.jsonl` separately.

The known missing retro March 3–4, 2026 source dates may be deferred and are
explicitly recorded; other interior hourly source gaps fail the job. Existing
NRT files for those dates are included normally. A later rerun picks up repaired
source dates and skips unchanged accepted outputs. Summaries are subset where
available, not manufactured when absent from the parent. This is a backfill,
not a new cron hook, domain promotion, or authorization to start B120 modeling.

Initial backfill submitted October 27, 2026 as **5031344**, initially pending
resources. The full regression suite passed (528 passed, 2 skipped), followed
by an additional passing source-receipt guard test. Real-data chained-crop
acceptance and publication are performed by the job, not implied by submission.

Initial backfill **5031344** subsequently passed: 35,548 files published, zero
failures, with only retro March 3–4 deferred. After CONUS repair and accepted
CNRFC propagation of both dates, catch-up **5037258** was submitted with the
same 64-CPU/16-worker configuration and a two-hour limit. It rescans both streams
and all three resolutions, skipping unchanged accepted files. Its job-specific
report must confirm publication and no remaining deferred gaps before calling
the catch-up complete; absent parent summaries are not created by subsetting.

## B120 model production

The forcing catch-up passed (35,550 files considered; three published, 35,547
skipped, zero failures and no deferred hourly gaps). The authorized model
campaign uses **32 MPI ranks**, one thread per rank, ordinary `shared-128`
resources and 120 GB requested node scratch. No NRTRES reservation is used.

`bin/submit_b120_production.py` submits a short acceptance gate followed by
sequential annual jobs for January 2, 1979 through **April 18, 2026, 00 UTC**.
The final forcing date supplies the endpoint hour. The submission journal is
`nwm/status/b120/production_v1/submission.json`; duplicate submission is refused.
Every annual job depends on the preceding job succeeding and checks the warm
gate again; years after 1979 also require the previous annual acceptance.

The gate subsets the preserved CONUS spin-up initialization to B120, retaining
its physical state and history-counter convention. A January 2–5 warm-start
run is compared with a January 4–5 continuation for eight land/routing state
fields (relative tolerance 1e-5, absolute tolerance 1e-6). Parameter hashes,
forcing receipts, active-cell values, output records and daily channel means
are checked. A failed gate prevents all annual jobs from running. Existing
initialization directories are not overwritten on retries: inspect any failed
gate's evidence before arranging a replacement.

`bin/run_b120_production.py` and `slurm/run_b120_production.sh` retain the
validated shared production mechanics: `rst_typ=1`, `RSTRT_SWC=0`,
`GW_RESTART=1`, terrain/channel time steps 600/600 seconds, lakes disabled,
daily LDASOUT and both hourly/daily CHRTOUT. Hourly records are grouped into
calendar-day files; daily reductions retain the model's 01–00 UTC interval-end
semantics. Monthly paired restart checkpoints and monthly acceptance gates
allow safe retries. Annual jobs request 24 hours; the gate requests two hours.
Actual throughput will be measured rather than inferred from CNRFC timings.

Outputs/restarts/runs use `nwm/{outputs,restarts,runs}/b120/retro/production_v1/`.
Gate outputs are isolated under `nwm/outputs/b120/tests/`. The runner permits
only this B120 retro campaign; NRT transition testing and operational cron
registration remain separate future steps. No shared executable is rebuilt,
and CNRFC/CONUS publication paths and running chains remain unchanged.

Submitted October 27, 2026: gate **5037306**, followed by annual jobs
**5037307–5037354** (1979–2026). These are conditional submissions, not model
acceptance. The full regression suite passed (532 passed, two skipped), focused
runner tests passed (15), and Ruff/shell/diff checks passed before submission.
