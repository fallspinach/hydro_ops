# CONUS spin-up status and accepted routing limitation

## Completed spin-up

The 1981–1985 chain completed with 120 MPI ranks, 600-second terrain/channel
timesteps, and no history output. Successful jobs: 4495260, 4506197, 4506198,
4506202, and 4506203. The successful 1982 run replaced the failed original attempt.
Combined model runtime was approximately 93 hours 51 minutes.

Retain the paired restart at **1986-01-01 00 UTC**:

- `nwm/restarts/conus/retro/1986/01/RESTART.1986010100_DOMAIN1`
- `nwm/restarts/conus/retro/1986/01/HYDRO_RST.1986-01-01_00:00_DOMAIN1`

Audit array 4523492 and summary 4523497 screened all five year-end pairs.
Reports: `nwm/status/spinup-restart-audit/job_4523492/`. Checked core active-land
states had no missing values or broad-bound violations; liquid soil water never
exceeded total soil water. Routing variables were inventoried, not subjected to
comprehensive physical acceptance limits. These checks do not establish
hydrologic equilibration or validate every local routing result.

## Accepted local hydrography/routing limitation

Reach **5279242**, approximately **32.07719 N, 94.20493 W** in eastern Texas,
has `hlink = 619.396 m` and discharge about `167.727 m3/s` in the final restart.
`hlink` is channel flow depth above the bed, not absolute water-surface elevation.
Supplied geometry: bottom width 1.070 m, bankfull top width 1.784 m,
compound-channel width 5.351 m, slope 0.00001 m/m. These suspicious depth/geometry
results must not be interpreted as realistic inundation depths. Other year-end
inventories also contain extreme depths (maximum about 838 m); not all have been
attributed to this particular reach or mechanism.

The project owner inspected reach 5279242 and reported a connector between two
streams that later converge, forming a loop-like hydrographic configuration.
This is a plausible explanation for local routing problems, but the precise
mechanism has not been independently demonstrated. Do not describe it as a
confirmed directed cycle or a fully diagnosed solver defect.

**Project decision:** accept this documented local limitation and continue using
the retained spin-up restart. Do not edit hydrography, patch the restart, rerun
spin-up, or block subsequent work solely to resolve this issue. Avoid interpreting
affected reach depths as inundation estimates. Neighboring/downstream effects
remain unquantified; acceptance does not establish that such effects are absent.
Revisit only if future application needs or consequential downstream errors
justify further investigation. No model/data changes accompany this decision.

## Groundwater interpretation

The final spin-up namelists use `RUNOFF_OPTION=7` (Noah-MP Xinanjiang surface
runoff with bottom soil drainage), `GWBASESWCRT=4` (WRF-Hydro exponential buckets
with area-normalized parameters), `bucket_loss=0`, and `GW_RESTART=1`.
Lake routing is disabled.

Noah-MP `ZWT` is 2.5 m throughout the screened active domain in every year-end
restart. The selected runoff option does not activate Noah-MP's dynamic
water-table schemes, consistent with an unused/default state rather than failure
of groundwater evolution. The separate bucket state is `z_gwsubbas`, whose
inventory changes between years.
