# CONUS retrospective simulation: gated production

**Hourly CHRTOUT archive acceptance is revoked pending repair.** The
[record-dimension recovery investigation](chrtout_archive_recovery.md) found
that calendar collections retained a single channel array despite multiple time
coordinates. The original 1988–2002 chain was held; the old 1987 allocation was
cancelled after preserving 720 raw June hourly files and its restart files.
Recovery uses a separate `recovery_record_stack_v1` campaign and the corrected
publisher; see the investigation for launch records. Native daily land outputs and
restart checkpoints are not implicated by this publication bug. Do not derive
daily channel statistics from the affected hourly collections.

The initial campaign starts **1979-01-02 00 UTC**, excluding the incomplete
1979-01-01 forcing day. It recycles the accepted 1986-01-01 spin-up restart
state, not a historical 1979 state estimate. The accepted local hydrography
limitation in [the spin-up assessment](conus_spinup_status.md) remains; it is
not repaired or used to reject this initialization.

## Acceptance and automatic progression

Submit with `bin/submit_conus_retro_simulation.py --campaign production_1979_v1
--end-year 1980 --submit` (omit `--submit` to preview).

The first job runs January 2 00 UTC through January 4 00 UTC, then continues
from that restart through January 5 00 UTC. Dependent annual jobs follow only
after both segments pass. Both SLURM `afterok` and an explicit test acceptance
marker are required. Acceptance checks include exact 48-hour CHRTOUT coverage,
two daily LDASOUT records (plus one on continuation) with correct UTC bounds and variable-level reduction
metadata, finite active-cell core land states, routing state screening, and
both correctly dated terminal restarts. This is an engineering acceptance
test, not independent scientific validation or proof of spin-up convergence.

The annual 1979 run starts again from January 2; the small test is deliberately
not spliced into the production archive. Later years use chronological
production checkpoints. The automatic chain has been extended through 2002.
Use `--extend --end-year 2002` with the existing campaign to append years
without resubmitting its acceptance test or existing annual jobs. Every new
year depends on the preceding year's successful completion; extending a
blocked chain does not override its acceptance gate.

Each job requests one node, 120 MPI ranks, 240 GB reserved scratch and a 48-hour
limit (four hours for the test). Routing remains enabled at 600/600 seconds;
PCP_PARTITION_OPTION=1, lakes off, t0OutputFlag=0. Output-enabled annual runtime
is not yet measured; the earlier output-free spin-up timing is not a guarantee.

## Restart safety and monthly execution

### Daily channel output enabled for remaining years

The production runner now enables **both `CHRTOUT_HOURLY=1` and
`CHRTOUT_DAILY=1`**, retaining daily-only LDASOUT. At the change, CONUS year
1987 (job 4524572) was running with the already-loaded old runner. That allocation
was subsequently cancelled after scratch preservation during the archive incident.
The original 1988–2002 chain must not be released simply to adopt this setting;
recovery requires its own acceptance gate and corrected archive publication.
Recovery campaigns enable both channel resolutions. Existing completed artifacts
are not automatically recalculated by changing the runner configuration.

The model computes daily channel reductions internally, using per-variable
`cell_methods` and 00–00 UTC bounds. The runner requires one daily record for
every simulated day, validates bounds, finite values and reduction metadata,
and publishes `YYYYMMDD.CHRTOUT_DOMAIN1.daily` beside daily LDASOUT under
`production/daily/YYYY/MM/`. Monthly reports retain `daily_records` for LDASOUT
and add `daily_channel_records`. Missing or invalid daily channel output blocks
monthly acceptance rather than being silently discarded. Hourly publication and
restart logic are unchanged. Simultaneous output is already exercised by the
CNRFC production campaign; there is no separate daily-channel postprocessing job.

## Monthly checkpoint behavior

Each annual job runs successive calendar-month segments. Only the segment's
terminal 00 UTC restart pair is written, at the first of the next month. This
handles unequal month lengths without fixed 30-day approximations. Completed
months have an `accepted.json` marker; resubmitting the same campaign/year skips
those months after checking their restart pair. No automatic failure retries
are submitted by this initial controller.

The original spun-up restart files are never edited. An initialization copy
has its land Times/START_DATE and hydro Restart_Time/Since_Date adjusted to
1979-01-02; eight land accumulation diagnostics are reset. The hydro history
counter is preserved unchanged. All other variable values are compared to the donor before use.
Subsequent chronological resumes preserve accumulation state with RSTRT_SWC=0.

The first acceptance job (4523923) finished model integration but failed output
coverage validation. Source tracing identified an initialization bug: resetting
`his_out_counts=0` conflicts with the warm-restart reader's `out_counts=1`,
advancing the schedule an extra hour at t0 even with t0OutputFlag=0. The fix
preserves the donor counter without changing Fortran. New initialization copies
are under `initialization-preserved-counter/`, leaving the failed test's donor
copy and logs intact. Tests reject old zero-counter cached initializations.
Each model invocation now saves an `output-inventory.json` before validation,
including filenames and decoded times, so evidence survives scratch cleanup.
Replacement acceptance job **4524600** tests January 2–4 plus a January 4–5
continuation on 120 ranks. Existing 1979 job **4523924** now depends on its
success; the rest of the chain through 2002 is unchanged. The explicit gate
also requires `continuation_passed=true` before any production year can run.

Job 4524600 confirmed all 48 initial hours and 24 continuation hours, but failed
when publishing the continuation. NCO inherited the first input's `time:valid_max`,
which ended at January 4 00 UTC; valid later times were consequently masked by
NetCDF readers. Calendar publication now verifies raw time values against the
expected hours, updates valid_min/valid_max and archive coverage metadata, and
checks decoded timestamps again. It never repairs incorrect time values by
changing metadata. Regression tests include different input validity ranges and
rejection of incorrect coordinates.

Retry **4524825** reuses the accepted two-day segment and its January 4 restart,
rerunning only January 4–5. The failed continuation logs and candidate archive
are retained with `failed-4524600` names. Production job 4523924 now waits for
4524825 and the full acceptance marker; all later dependencies are unchanged.

Restart files live under
`nwm/restarts/conus/retro/<campaign>/production/YYYY/MM/`;
the separate acceptance-test pair is under `<campaign>/acceptance/`.
No hourly or day-of-month restart subdirectories are produced.

## Output temporal resolution versus file grouping

- `nwm/outputs/conus/retro/<campaign>/production/daily/YYYY/MM/` contains
  native `YYYYMMDD.LDASOUT_DOMAIN1.daily` files, one daily-resolution record,
  and `YYYYMMDD.CHRTOUT_DOMAIN1.daily` for runs using the new dual-channel setting
  (including the separate recovery campaigns).
  Reduction is specified by each variable's `cell_methods`, not a blanket mean.
- `.../production/hourly/YYYY/MM/` contains
  `YYYYMMDD.CHRTOUT_DOMAIN1`, with hourly-resolution records grouped 00–23 UTC.
  Native hourly files are written on node scratch and compressed into these
  collections before permanent publication. SPLIT_OUTPUT_COUNT stays 1 because
  the current native CHRTOUT writer does not implement calendar-day collections.
- Test outputs substitute `acceptance` for `production`.

Physical daily reduction uses interval-end samples 01 UTC through next-day
00 UTC. File grouping uses timestamp date, 00–23 UTC. At a month boundary,
the preceding run publishes the next day's single 00 UTC record; the next
segment merges it with hours 01–23. Initial January 2 has only 01–23 because
t0 output is off. A terminal checkpoint day has only 00 until continuation.
The archive's `calendar_day_complete` attribute exposes these partial files.

Original scratch outputs are removed only after output validation, archive
timestamp readback, and restart publication. Model logs and namelists remain
under `nwm/runs/conus/retro/<campaign>/<kind>/YYYYMM/`; SLURM logs and job IDs
are under `nwm/logs/wrf_hydro/conus/retro/<campaign>/`.

Publication is atomic per file, not across a whole month. If interrupted during
publication (rather than during model integration), the overlap guard can
require operator review before retry. It refuses to overwrite complete hourly
archives silently. This fail-closed behavior is intentional for this first
production campaign.
