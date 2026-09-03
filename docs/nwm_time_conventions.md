# NWM forcing, output, and restart time conventions

This project uses UTC exclusively. Storage boundaries, model integration boundaries, and
temporal reductions are related but are not the same operation.

## Canonical operational timeline

For UTC day `D`:

| Object | Convention |
|---|---|
| Hourly forcing storage | The file named for `D` contains records timestamped `D 00` through `D 23`. |
| Hourly model-output storage | Calendar chunks, when retained, contain timestamps `D 00` through `D 23`. |
| Model initialization | The paired Noah-MP and routing restart is valid at `D 00`. |
| 24-hour model integration | The model advances to endpoint times `D 01` through `D+1 00`. |
| Forcing consumed | `D 01` through `D+1 00`; the reader crosses two calendar storage files. |
| Final restart | The paired restart is valid at `D+1 00`. |
| Model-interval daily reduction | The 24 completed endpoint samples `D 01` through `D+1 00`, with bounds `[D 00,D+1 00]`. |

The daily product is associated with `D`, the start of its bounded interval. Calling this an
"01-00 day" is discouraged: the physical interval is the ordinary UTC day `[00,24)`, while the
samples are labeled at completed timestep endpoints.

## Initial output

Operational runs set `t0OutputFlag = 0`. With `t0OutputFlag = 1`, WRF-Hydro writes the initial
restart state at `D 00` in addition to the 24 post-integration outputs ending at `D+1 00`. The
initial record duplicates the preceding run's terminal timestamp and must never enter a completed-
timestep reducer. It remains useful only for explicit diagnostics.

## Storage versus reduction

Hourly calendar chunks remain `00-23` because they are predictable, interoperable, and easy to
inspect. A model-interval reducer reads hours `01-23` from the chunk for `D` and hour `00` from
the chunk for `D+1`. Neither input nor output hourly storage is reorganized to `01-00`.

Daily products must publish all of the following metadata:

- interval bounds `[D 00,D+1 00]`;
- a representative midpoint coordinate at `D 12`;
- sample endpoint range `D 01` through `D+1 00`;
- sample count and cadence;
- `day_definition = "model_interval"`;
- a variable-level `cell_methods` reduction;
- source-file, restart, software-version, and correction provenance where applicable.

## Variable semantics

The reduction method belongs to each variable, not to the file:

- `time: mean` for states and rates;
- `time: sum` for independent per-timestep amounts;
- `time: last` for end-of-interval states and categorical/status fields;
- end-minus-start differencing for running counters;
- omission where no scientifically defensible daily reduction exists.

Running accumulations must not be summed. Rate integration must include the timestep and must
change the output units to the corresponding amount.

## Completeness and publication

A product for `D` is complete only after the `D+1 00` endpoint exists. The reducer requires each
of the 24 expected timestamps exactly once and refuses missing, duplicated, irregular, or out-of-
order samples. Production writes occur on node-local scratch, followed by validation and atomic
promotion. Partial products are diagnostic-only and are never published into operational streams.

The operational run planner should prefer 00 UTC paired restarts, preflight forcing through the
terminal endpoint, and rewind according to the NRT lookback policy. A non-midnight emergency
restart may continue the simulation, but a daily product spanning that restart is publishable only
after comparison with an uninterrupted reference or after accumulator state is made restart-aware.

Generate a canonical plan before staging a run:

```bash
python bin/plan_wrf_hydro_run.py --start 2026-09-01T00:00:00+00:00 --hours 48
```

Create a forcing summary matching one model interval:

```bash
python bin/reduce_forcing_model_day.py \
  --input-root outputs/forcing/nwm/nrt \
  --day 2026-09-01 \
  --output outputs/forcing/nwm_summary/nrt/2026/09/20260901.forcing_summary.nc
```
