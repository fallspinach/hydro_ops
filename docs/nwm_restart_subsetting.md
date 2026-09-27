# Subsetting CONUS restart states for regional WRF-Hydro runs

Use `bin/subset_nwm_restart.py` to initialize a regional domain from a matching
CONUS land/hydro restart pair. This transfers the existing model state; it is not
a cold start, a new spin-up, or a guarantee of identical subsequent regional and
CONUS results. The CNRFC warm-start pilot passed (results below).

## Supported configuration

The implemented and tested configuration is our coupled WRF-Hydro 5.4.0 build,
Noah-MP on the 1-km NWM grid, 250-m terrain routing, reach-based routing with
UDMP groundwater, and lakes disabled. The subset must use the same underlying
CONUS parameter version, grid alignment, physics and state schema.

Inputs are a land `RESTART.YYYYMMDDHH_DOMAIN1`, a hydro
`HYDRO_RST.YYYY-MM-DD_HH:MM_DOMAIN1`, the **parent run's** RouteLink file, and the
regional parameter directory containing `domain_masks.nc` and `RouteLink.nc`.
The restart arrays do not contain reach IDs: supplying the correct parent
RouteLink ordering is essential and cannot be inferred from the restart alone.

| State | Extraction rule |
| --- | --- |
| Noah-MP land states | Crop `south_north`/`west_east` using the shared 1-km window |
| Staggered land dimensions | Same origin, with the extra edge retained |
| Hydro land-grid states | Crop `iy`/`ix` with the same 1-km window |
| Terrain routing states | Crop `iyrt`/`ixrt` with the aligned 4× window |
| `hlink`, `qlink1`, `qlink2` | Match parent `link` IDs in regional RouteLink row order |
| `z_gwsubbas` | Same reach mapping: in this UDMP configuration it uses `links` |

Do not sort restart arrays by feature ID as for CHRTOUT. Do not select groundwater
states using the regional GWBUCKPARM row numbers: that table can have fewer
catchments than the routing network has reaches. For CNRFC the restart retains
301,644 reaches, while the extracted runoff catchment set has 297,805 entries.

This ordering follows the local model implementation in
`src/Routing/module_HYDRO_io.F90` (`w_rst_crt_reach`,
`read_rst_crt_reach_nc`, and the `z_gwsubbas` declaration) and
`src/MPP/module_mpp_ReachLS.F90` (reach gather/scatter), under
`external/wrf_hydro_nwm_public-v5.4.0/`. Recheck these assumptions after a model
upgrade or changing routing/groundwater options.

## Extraction example

Run on a compute node with node-local scratch. From the project root, after
loading the project Python environment, use a **new** output directory:

```bash
python bin/subset_nwm_restart.py \
  --land nwm/restarts/conus/retro/production_1979_v1/production/1979/02/RESTART.1979020100_DOMAIN1 \
  --hydro nwm/restarts/conus/retro/production_1979_v1/production/1979/02/HYDRO_RST.1979-02-01_00:00_DOMAIN1 \
  --parent-routes nwm/static/operational/nwm.v3.1.6/domain/RouteLink_CONUS.nc \
  --parameters nwm/static/domains/cnrfc/parameters \
  --output /scratch/${SLURM_JOB_USER}/job_${SLURM_JOB_ID}/cnrfc-initial-restart
```

The original filenames are retained, with a `subset_restart.json` extraction
report. Existing output directories/files are rejected. Each file is written to
a partial path and renamed after verification, but the **pair is not published as
one transaction**. A failed run may leave diagnostic files or a completed first
file: do not use an incomplete directory. Require a passed extraction report,
then the pair/time/state checks below, before using or archiving the result.

The extractor copies raw values without scale/mask reinterpretation, uses bounded
128-row blocks for gridded states, writes compressed NetCDF4, and compares every
selected value during writing and again after reopening. It preserves attributes,
restart timestamps, accumulated states, and counters, including `his_out_counts`.
It checks source sizes/mtimes for changes during extraction. It neither modifies
the CONUS files nor fills, zeros, or reinitializes states outside the regional
model mask. Model activity remains defined by the regional parameter file.

## Warm-start launch checks

The standalone extractor checks extraction fidelity, **not** the complete run
configuration. The tested runner `bin/test_cnrfc_model.py --warm-start` additionally:

1. Verifies the regional parameter extraction checksums and model-mask agreement.
2. Checks land `Times` and hydro `Restart_Time` against the same initialization UTC.
3. Checks required restart variables and finite, nonmissing active land states.
4. Checks forcing coordinates, required hours, and all eight fields on active cells.
5. Runs with the extracted land and hydro files and validates final outputs/states.

Warm-start settings include `RESTART_FILENAME_REQUESTED`, `RESTART_FILE`,
`GW_RESTART=1`, `RSTRT_SWC=0`, and `rst_typ=0`. Keep `t0OutputFlag=0`; preserve
model counters rather than editing them to change output grouping. Initialize and
finish at 00 UTC. For a run February 1 00 through February 3 00, the model consumes
hourly endpoints through February 3 00, so the February 3 calendar-day forcing
file must also be available. Daily/monthly forcing summaries cannot drive the model.

For the reproducible CNRFC pilot, submit from the project root:

```bash
sbatch slurm/test_cnrfc_warm_start.sh
```

This script intentionally repeats the fixed February 1979 test, not an arbitrary
production run. It uses 32 MPI ranks, 120 GB reserved scratch, a four-hour limit,
600/600-second terrain/channel timesteps, precipitation partition option 1, and
no lakes/diversions. Parameters and intermediate model outputs are staged on scratch.
Accepted hourly CHRTOUT is collected into calendar-day files; LDASOUT is daily
resolution. Only the final 00 UTC restart is archived for this two-day test.

## Acceptance evidence

Job **4638507 completed successfully** in 3m38s scheduler elapsed time; model
runtime was **94.1 seconds** and total runner time **216.8 seconds**, including
restart extraction, preflight, model execution, audits and publication. The run
covered **1979-02-01 00 UTC through 1979-02-03 00 UTC**, on a 1332×850 land grid
with 674,999 active cells. Checks passed for 48 hourly channel records, two daily
land records with correct bounds, and the terminal land/hydro restart pair.

Evidence and test products:

```text
nwm/outputs/cnrfc/retro/tests/warmstart_19790201_48h/job_4638507/
  acceptance.json
  initial_restart/subset_restart.json
  initial_restart/RESTART.1979020100_DOMAIN1
  initial_restart/HYDRO_RST.1979-02-01_00:00_DOMAIN1
  model.log
  namelist.hrldas
  hydro.namelist
  hourly/
  daily/
  restarts/1979/02/
```

The corresponding cold-start test (job 4638500, January 2–4) also passed, with
77.2 seconds model runtime. These are short structural/I/O tests, not independent
hydrologic validation or proof of long-run stability.

## Limitations

- No reservoir/lake or nudging restart extraction is implemented. Lake states
  and basin-indexed groundwater arrays are rejected rather than guessed; other
  model schemas/configurations require a separate review.
- CONUS state carries its original history. Do not relabel dates or reset
  accumulations for ordinary chronological warm starts.
- Inherited global projection-origin attributes remain parent metadata; use the
  regional parameter grid for geolocation. The extractor does not reconstruct
  those attributes or insert new grid coordinates.
- Regional boundaries truncate contributing areas and upstream inflows. Transferred
  state is initially faithful to CONUS, but future regional flows can differ,
  particularly on boundary-affected reaches. No boundary inflow is invented.
- The CNRFC reporting region remains the unbuffered official-boundary/forecast-basin
  union; its 20-km buffer supplies supporting area, not guaranteed drainage closure.

See [CNRFC domain geometry, masks and parameters](cnrfc_domain_boundary.md).
