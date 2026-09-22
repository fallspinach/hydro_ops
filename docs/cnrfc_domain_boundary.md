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
  forcing/outputs/conus/retro/1981/01/19810101.LDASIN_DOMAIN1 \
  forcing/outputs/cnrfc/retro/1981/01/19810101.LDASIN_DOMAIN1 \
  --domain-masks nwm/static/domains/cnrfc/masks/cnrfc_masks.nc
```

This is a command example, not a claim that the CNRFC archive has been produced.
The tool crops every spatial variable to the exact same window, masks the eight
forcing fields outside `forcing_mask`, retains time coordinates, and compares all
retained values against the source. It rejects missing active-cell values without
attempting interpolation or gap filling. Outputs include both masks and a separate
`.subset.json` audit; inherited CONUS mask/audit attributes are renamed with a
`parent_` prefix so they do not masquerade as subset audits. Failed candidates are
not published. The caller must give a new output path.

Six focused tests passed (mask relationships, bad coverage/alignment, grid-window
selection and real NCO/NetCDF small-grid extraction). No full-size forcing pilot
is implied by those tests. Full parameter extraction passed separately as above.
