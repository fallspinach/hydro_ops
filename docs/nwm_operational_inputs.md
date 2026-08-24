# NWM 3.1 operational inputs

## Authoritative public package

NCEP/NCO publishes the current operational implementation package as `nwm.v3.1.6`. This is the
patch release of the NWM 3.1 production workflow, distinct from the WRF-Hydro model-code tag
`v5.4.0`. The public parameter tree is:

```text
https://www.nco.ncep.noaa.gov/pmb/codes/nwprod/nwm.v3.1.6/parm/
```

`bin/download_nwm_operational_inputs.py` retrieves the CONUS full-routing static files,
operational tables, analysis namelists, reservoir indexes, RouteLink hydrofabric, and diversion
definition into `data/static/nwm/operational/nwm.v3.1.6`. Downloads resume through `.part`
files, require exact server content lengths, and produce a SHA-256 manifest.

The selected files total approximately 34 GB. Long-range alternatives are deliberately omitted
because this project uses the full 250-m terrain-routing configuration.

## Included components

- 1-km Noah-MP grid and initial static land fields: `geo_em_CONUS.nc`, `wrfinput_CONUS.nc`.
- 250-m routing terrain: `Fulldom_CONUS_FullRouting.nc`.
- NHDPlus-derived reach hydrofabric and channel parameters: `RouteLink_CONUS.nc`.
- NWM 3.1 diversion definition: `Diversion_CONUS.nc`.
- Groundwater, soil, and spatial routing parameters.
- Land-grid metadata and catchment spatial weights.
- Nudging parameters and configuration-specific reservoir indexes.
- Operational parameter tables and analysis-assimilation namelists.

## Inputs not publicly disseminated with the package

The analysis namelist references `LAKEPARM_CONUS.nc`, but the file is absent from the public
`parm/domain` directory. Operational scripts treat some reservoir/lake parameters as dynamically
managed inputs. The public package therefore does not yet constitute a completely runnable
CONUS analysis domain.

Current paired Noah-MP and hydro routing restarts are also not disseminated by NOMADS or the
NWM NODD forecast bucket. Those services publish land, terrain, reservoir, and channel output,
which are not complete restart states. Reconstructing a restart from those products would omit
internal snow, soil, routing, groundwater, reservoir, nudging, and accumulator state and is not
a scientifically valid initialization.

NCO publishes a `manualclimateRestart` directory containing a hydro-only state dated
2016-04-24 and split across 768 ranks. It lacks the matching land restart, is decomposition
specific, and predates the current domain/parameters; it must not be mixed with this setup.

## Initialization strategy

Use one of these approaches, in order of preference:

1. Obtain a matching generic land, hydro, and nudging restart set plus the dynamic lake parameter
   file directly from NOAA OWP/NCO for the `nwm.v3.1.6` CONUS domain.
2. If those are unavailable, cold-start the current public domain and perform a documented
   multi-year spin-up using this project's continuous non-forecast forcing. Repeat the forcing
   period if needed until soil moisture, groundwater storage, snow, and streamflow climatology
   stabilize. Save a project-owned paired restart at the target experiment boundary.
3. For development while CONUS inputs are incomplete, use the verified official Croton case or
   construct a watershed subset and spin it up independently.

Do not initialize from operational history output or combine restart components from different
NWM versions, parameter revisions, timestamps, domains, or MPI decompositions.

## Remaining acceptance checks

Run `bin/inventory_nwm_operational_inputs.py` after download. It writes a machine-readable report
to `outputs/inventory/nwm.v3.1.6-domain.json`, opens every one of the 15 NetCDF domain files,
records its format, dimensions, and variables, checks the minimum WRF-Hydro 5.4 NWM structural
interface, and compares the four 1-km land-grid dimensions. Missing downloads remain `pending`;
schema or NetCDF failures are `incompatible`.

The inventory is deliberately a structural gate, not proof of scientific consistency. Before a
model run, add identifier-set checks across RouteLink, groundwater, reservoir, diversion, and
nudging files once their NWM 3.1 schemas have been observed, and compare projection/grid metadata
and sampled coordinate values. The report separately retains the missing dynamic lake file and
restart set as run blockers.

We will finish and validate the forcing production workflow before constructing or testing a
CONUS WRF-Hydro run. After initialization is eventually available, require water and energy
balance diagnostics and restart continuity tests before production simulations.
