## 1. Informed forcing for CONUS-wide NWM



Project references: docs/forcing_production_workflow.md; docs/nrt_gfs_operations.md; docs/nwm_time_conventions.md.


## 2. Better informed—not simply higher resolution



Project references: docs/forcing_production_workflow.md; docs/nrt_gfs_operations.md; docs/nwm_time_conventions.md.
NLDAS forcing: https://ldas.gsfc.nasa.gov/nldas/v2/forcing

## 3. Eight fields, one model-ready product



Project references: docs/forcing_production_workflow.md; docs/nrt_gfs_operations.md; docs/nwm_time_conventions.md.


## 4. One production chain, two published streams



Project references: docs/forcing_production_workflow.md; docs/nrt_gfs_operations.md; docs/nwm_time_conventions.md.


## 5. Observation-informed inputs

MRMS current operational tables indicate approximately 20-minute Pass 1 and 60-minute Pass 2 latency. Older versions used approximately 60 and 120 minutes. Stage-IV availability and revisions vary by RFC and accumulation period; no universal latency is asserted. PRISM uses the 4-km archive in this project, although other resolutions exist.

Project references: docs/forcing_production_workflow.md; docs/nrt_gfs_operations.md; docs/nwm_time_conventions.md.
MRMS: https://www.nssl.noaa.gov/projects/mrms/operational/tables.php
MRMS versions: https://inside.nssl.noaa.gov/mrms/past-code-updates/
Stage IV: https://www.emc.ncep.noaa.gov/mmb/research/stage4.FAQ.html
PRISM: https://www.prism.oregonstate.edu/calendar/
PRISM grids: https://prism.oregonstate.edu/data/

## 6. Complete meteorology across time and space



Project references: docs/forcing_production_workflow.md; docs/nrt_gfs_operations.md; docs/nwm_time_conventions.md.
NLDAS: https://ldas.gsfc.nasa.gov/nldas
NLDAS forcing: https://ldas.gsfc.nasa.gov/nldas/v2/forcing
HRRR: https://emc.ncep.noaa.gov/emc/pages/numerical_forecast_systems/hrrr.php
GFS: https://www.nco.ncep.noaa.gov/pmb/products/gfs/

## 7. The same date becomes better informed



Project references: docs/forcing_production_workflow.md; docs/nrt_gfs_operations.md; docs/nwm_time_conventions.md.
PRISM: https://www.prism.oregonstate.edu/calendar/
NLDAS: https://ldas.gsfc.nasa.gov/nldas
MRMS: https://www.nssl.noaa.gov/projects/mrms/operational/tables.php

## 8. Precipitation: select locally, constrain daily



Project references: docs/forcing_production_workflow.md; docs/nrt_gfs_operations.md; docs/nwm_time_conventions.md.


## 9. PRISM changes the amount—not the storm clock

Illustration only, not observed data. Production uses PRISM 12–12 UTC accumulation windows, then regroups output into calendar-day files. Real reconciliation includes validity, dry/wet, scaling and domain checks, so not every cell follows an unconstrained ratio.

Project references: docs/forcing_production_workflow.md; docs/nrt_gfs_operations.md; docs/nwm_time_conventions.md.
PRISM: https://www.prism.oregonstate.edu/calendar/

## 10. Terrain corrections stay physically coupled



Project references: docs/forcing_production_workflow.md; docs/nrt_gfs_operations.md; docs/nwm_time_conventions.md.
Cosgrove: https://doi.org/10.1029/2002JD003118

## 11. The northern HRRR gap must not stop NWM

Diagram is conceptual, not a geographic map. Actual coverage uses cached native-HRRR geometry and the versioned static envelope. Do not infer a latitude boundary from the rectangles. NLDAS-2 selection is per hour. GFS uses one meteorological bundle/cycle and elevation adjustments; valid precipitation is preserved. Missing required GFS fails the affected update, retaining previously accepted files.

Project references: docs/forcing_production_workflow.md; docs/nrt_gfs_operations.md; docs/nwm_time_conventions.md.
GFS: https://www.nco.ncep.noaa.gov/pmb/products/gfs/

## 12. NRT and Retro answer different questions



Project references: docs/forcing_production_workflow.md; docs/nrt_gfs_operations.md; docs/nwm_time_conventions.md.


## 13. NRT: refresh → replace only what changed



Project references: docs/forcing_production_workflow.md; docs/nrt_gfs_operations.md; docs/nwm_time_conventions.md.


## 14. Scheduled opportunities ≠ current-hour delivery

Source: cron/hydro_ops.crontab and bin/update_nwm_forcing.py cycle_window, checked 2026-09-19. Six-hourly target scan 10 days with 14-day source repair lookback; daily 200 days; recent GFS worker tail seven days. Both NRT lanes end at UTC today minus two days. Monthly retro scans 45 target days ending today minus 183 days and still requires stable PRISM acceptance. These are scan windows, not mandatory rebuild counts. Faster event-driven/current-day delivery and daily retro eligibility scans are proposed, not activated.

Project references: docs/forcing_production_workflow.md; docs/nrt_gfs_operations.md; docs/nwm_time_conventions.md.


## 15. Retro: stable constraints, reproducible history



Project references: docs/forcing_production_workflow.md; docs/nrt_gfs_operations.md; docs/nwm_time_conventions.md.

1979-01-01 is a partial 11-hour forcing day because NLDAS begins at 13 UTC. It is not a full-day model start. Cleanup is deferred in the active 2003–2020-10-13 campaign. Stable PRISM can change under a major source dataset release; preserve version provenance. HRRR anomaly refinement is not validated/enabled as a retrospective default.

## 16. One daily file; two clocks to understand



Project references: docs/forcing_production_workflow.md; docs/nrt_gfs_operations.md; docs/nwm_time_conventions.md.


## 17. Quality safeguards users should know



Project references: docs/forcing_production_workflow.md; docs/nrt_gfs_operations.md; docs/nwm_time_conventions.md.


## 18. Find, inspect, then use



Project references: docs/forcing_production_workflow.md; docs/nrt_gfs_operations.md; docs/nwm_time_conventions.md.

Run from repository root in the hydro-ops environment: python bin/report_forcing_status.py. JSON: python bin/report_forcing_status.py --format json --output forcing/status/forcing-status.json. Status scans filenames/metadata, not a full scientific audit. Daily files deliberately have no .nc suffix. Baseline is an intermediate, not an alternative public stream.

## 19. What is ready—and what is not yet promised



Project references: docs/forcing_production_workflow.md; docs/nrt_gfs_operations.md; docs/nwm_time_conventions.md.


## 20. Source guide & further reading



Project references: docs/forcing_production_workflow.md; docs/nrt_gfs_operations.md; docs/nwm_time_conventions.md.

NLDAS: https://ldas.gsfc.nasa.gov/nldas
NLDAS forcing: https://ldas.gsfc.nasa.gov/nldas/v2/forcing
HRRR: https://emc.ncep.noaa.gov/emc/pages/numerical_forecast_systems/hrrr.php
MRMS: https://www.nssl.noaa.gov/projects/mrms/operational/tables.php
MRMS versions: https://inside.nssl.noaa.gov/mrms/past-code-updates/
Stage IV: https://www.emc.ncep.noaa.gov/mmb/research/stage4.FAQ.html
PRISM: https://www.prism.oregonstate.edu/calendar/
PRISM grids: https://prism.oregonstate.edu/data/
GFS: https://www.nco.ncep.noaa.gov/pmb/products/gfs/
Cosgrove: https://doi.org/10.1029/2002JD003118

