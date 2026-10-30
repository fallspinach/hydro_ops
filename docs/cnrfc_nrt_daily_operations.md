# CNRFC daily NRT model cycle

## Expanded-grid transition

The user approved [canonical expanded-CNRFC promotion](cnrfc_grid_promotion.md)
on October 29, 2026. The runtime migration gate blocks ordinary submissions until
promotion, all-resolution forcing synchronization, a two-day daily replay test
and audited old-grid retirement pass. The existing configuration then resumes
operations on `cnrfc`, grid version `cnrfc_hu18_v1`, using its configured promoted
handoff gate. Old-grid state and products must not be mixed with this grid.
Historical commissioning job references below describe the original grid;
current canonical paths and migration evidence are in the promotion guide.

The model runs once after a successful **daily** forcing cycle, summary refresh,
and NRT-only CNRFC synchronization. It is not triggered by the six-hourly forcing cycle.
The hook in `slurm/converge_nwm_forcing_cycle.py` uses `afterok` for all three
dependencies, even though forcing synchronization itself uses `afterany`.
Retro synchronization is a separate daily catch-up, not a model prerequisite;
historical subset failures must not block the current NRT replay.
`config/cnrfc_nrt_daily.json` is **enabled** following successful commissioning
and publication of job 4821570. Submission occurs once per UTC day, after the
daily forcing controller submits its summary and CNRFC synchronization jobs.
The model waits on successful completion of all three via SLURM dependencies.
No separate cron entry or crontab installation is required for this hook.

Routine runs use 64 MPI ranks, 120 GB scratch, a two-hour limit and `rst_typ=1`.
The scheduled command includes `--snapshot-forcing`, pinning the selected input
versions to avoid interference from later six-hourly updates. Newer revisions
are picked up by the next daily replay. This does not trigger a model run after
each six-hourly cycle. Existing submission deduplication and stream locks prevent
duplicate writers; active CNRFC jobs cause submission to defer. Failed dependencies
block the model, rather than allowing it to run on incomplete inputs. Inspect
the daily forcing manifest's `cnrfc_model_cycle` entry and
`nwm/status/cnrfc/nrt-daily/submission-YYYYMMDD.json` for submission status;
`latest.json` and its job-specific publication journal describe published results.
An uncertain submission or failed dependency requires inspection, not blind retry.

## Time and state

The planner selects the newest available forcing calendar day's 00 UTC as its
endpoint. The model preflight still verifies that endpoint's record, all required
intervening hours and variables, and current passed subset receipts. Partial
hours later that day are deferred. The desired start is 11 days before the end;
choose the newest paired 00 UTC checkpoint **at or before** that start. Never
skip a model/forcing gap after a prolonged outage. Never shorten an accepted run.

Before the corrected refresh, October 10's desired rewind was September 29,
but only September 1 was available at or before that date. Job 4821570 has now
published daily checkpoints, so routine cycles can use the intended 11-day
rewind instead of that 39-day monthly-checkpoint bootstrap.
The October 1–3 acceptance test is deliberately shorter and isolated from production.

Both land and routing restarts are written every 24 hours, checked, and stored
under `nwm/restarts/cnrfc/nrt/YYYY/MM/`. Daily LDASOUT and hourly/daily CHRTOUT
retain the established calendar-file and interval-end reduction conventions.
Native namelists use land restart frequency 24 hours and routing `rst_dt=1440`
minutes. Model physics, executable, 600/600-second routing, lakes-off and
`t0OutputFlag=0` remain unchanged. Cross-year replays split at January 1.

## Publication and recovery

`bin/run_cnrfc_daily_cycle.py` holds the existing NRT model stream lock through
simulation, validation and publication. Each job stages results under
`nwm/runs/cnrfc/nrt/cycles/job-JOBID/`; it does not overwrite canonical results
while the model is running. Each segment checks forcing identities, model success,
state, output coverage, daily streamflow consistency, all-variable archive
equality and every daily checkpoint. Forcing identities are checked again across
all accepted segments before publication.

Replay merges hourly records by timestamp: new records replace overlapping old
records; the starting day's unchanged 00 UTC record is preserved. This explicit
replay mode does not relax the normal publisher's overlap rejection. Merged files
receive the same full-variable comparison as ordinary archives.

Publication creates hardlink backups for **every existing destination** before
activation. Each file is atomically replaced with a validated same-filesystem
candidate, keeping its inode identity/receipt consistent. Ordinary exceptions
roll back changes; abrupt termination leaves a journal requiring inspection.
Publication is **not an atomic whole-directory snapshot**: consumers must check
`nwm/status/cnrfc/nrt-daily/latest.json` and avoid reading during `publishing`.
The next cycle refuses an incomplete publication gate. Consult the referenced
`publication/transaction.json` and its `previous/` backups before repair; do not
blindly resubmit an interrupted transaction. `cycle.json` records failed runs as
well as accepted publications. Historical catch-up reports remain provenance,
not the latest authoritative product state after a replay replaces their files.

## Historical commissioning and activation

The chronological notes below describe earlier disabled commissioning stages.
The current enabled state and scheduling behavior are described at the top.

The first test runs October 1–3, writes daily checkpoints, then re-runs October
2–3 from the new October 2 checkpoint. Required land/routing states are compared
against the uninterrupted endpoint (rtol 1e-5, atol 1e-6). It publishes only to
a private mirror of affected existing products, exercising overlap replacement
and backups without altering canonical NRT files. Automated tests also inject a
publication failure and verify rollback.

Commissioning job **4810248** was submitted for this isolated test. The complete
repository suite passed **510 tests** (one existing import deprecation warning).
The hook remains disabled until the model test and full-window commissioning
are inspected; no canonical publication or restart deletion is authorized by
the test job itself.

```bash
source bin/project_environment.sh
sbatch --job-name=wrfh-cnrfc-nrt-daily-checkpoint-replay-test \
  slurm/run_cnrfc_daily_cycle.sh --test --end-date 2026-10-03 --rewind-days 1
```

Before enabling the hook, require that test and a full-window commissioning run
to pass, and review its canonical publication journal. The scheduled submitter
deduplicates submissions per UTC day and defers when a CNRFC model job is active.
Failed/uncertain submissions require inspection; no unbounded automatic retry
is configured. Resources: 64 MPI ranks, 120 GB scratch, two-hour limit, ordinary
shared-128 resources. No NRTRES is requested for `mpan`.

Retention targets 30 days of daily paired checkpoints plus monthly anchors.
**Deletion remains plan-only during commissioning**, recorded in the cycle report.
Staging and previous-generation backups also remain recoverable until retention
and recovery handling have been exercised. No automatic cleanup of these data is
currently enabled. Routine operation and automatic cleanup must not be described
as activated while the configuration gate remains false.

## Restart-restoration investigation

Test 4810248 completed both model integrations and their individual acceptance
checks, but failed uninterrupted-versus-restarted state comparison before any
publication. All 9,001 SMC/SH2O mismatches were in 3,408 active cells (none in
inactive cells); maximum absolute difference was 0.0160785 m3/m3. Soil temperature
had 305 layer-value mismatches in 254 active cells, maximum 0.503845 K. Routing
state also differed. This is not an inactive-mask comparison artifact.

The saved namelists used `rst_typ=0`, `RSTRT_SWC=0`, `GW_RESTART=1`. The Noah-MP
reader restores land fields and invokes parameter initialization with restart
true (skipping cold-start soil initialization). However, the hydro restart reader
in `src/Routing/module_HYDRO_io.F90` restores coarse soil fields, `infxsrt`,
`sfcheadrt`, and `soldrain` only for `rst_typ=1`; coupling initialization copies
the coarse surface-water head to Noah-MP. Overland initialization starts that
head at zero. Fine-grid/channel/bucket restart restoration follows separate paths.

At the October 2 checkpoint, all active coarse soil values in the routing restart
(`smc1`–`smc4`, `sh2ox1`–`sh2ox4`, `stc1`–`stc4`) exactly matched their land
restart counterparts. Ponded head `sfcheadrt` was nonzero at 48,138 active cells;
2,905 of the 3,408 affected soil-moisture cells overlap them. This supports, but
does not alone prove, missing hydrologic-state restoration as the cause.

Isolated diagnostic job **4812565** replays October 2–3 from the original saved
checkpoint twice, with only `rst_typ` changed (0 control, 1 alternative). The
script checks unchanged forcing identities and compares both endpoints with the
original uninterrupted and resumed states. Reports are under
`nwm/runs/cnrfc/nrt/restart-diagnostics/job-4812565/`. No production namelist,
executable, restart or canonical output is changed by this diagnostic. Routine
scheduling remains disabled pending its result and subsequent acceptance.

### Confirmed diagnosis — job 4812565

The A/B diagnostic completed successfully. Repeating `rst_typ=0` reproduced the
original resumed state exactly in all eight checked fields, ruling out a
one-off MPI/run failure for this comparison. Changing only `rst_typ=1` removed
all above-tolerance differences from the uninterrupted endpoint:

| Field | Maximum absolute difference, `rst_typ=0` | `rst_typ=1` |
| --- | ---: | ---: |
| SMC / SH2O | 0.0160785 | 0 |
| SOIL_T | 0.503845 | 0 |
| SNEQV | 0.000152588 | 0 |
| hlink | 0.117856 | 0 |
| qlink1 / qlink2 | 0.135601 | 4.94e-40 |
| z_gwsubbas | 0.00129566 | 0 |

No mask mismatches occurred. All original soil-moisture mismatches were on active
cells; 3,408 cells are about 0.505% of the 674,999 active CNRFC cells. This was a
restart-restoration configuration issue, not an inactive-cell artifact, forcing
coverage problem, or justification for loosening the comparison tolerance.
`RSTRT_SWC=0` (preserve accumulations) and `GW_RESTART=1` were already appropriate
and remained unchanged in the diagnostic.

Recommended next implementation: use `rst_typ=1` for fully coupled warm restarts
with verified matching land/hydro restart pairs and terrain routing enabled;
rerun daily-checkpoint/replay commissioning before enabling the scheduled hook.
Production settings were **not changed** during this diagnosis. The earlier
monthly campaigns also used `rst_typ=0`, so their restart boundaries may carry
the same effect; no retrospective simulations have been invalidated, deleted or
resubmitted based on this single-period diagnostic.

## Adopted transition — 2026-10-10

The user approved retaining the existing retro history rather than rerunning
1979–2026. All newly launched fully coupled CNRFC/CONUS warm-start segments now
use **`rst_typ=1`**, with `RSTRT_SWC=0`, `GW_RESTART=1`, terrain routing enabled,
and paired land/hydro checkpoints. Cold-start benchmarks remain unchanged.
Future parameter-calibration reruns must use option 1. Acceptance reports record
the restart option. This is a configuration change, not a model executable change.

CONUS year 1988 (4665011) was already running with option 0 loaded in memory;
it is deliberately left untouched. Pending 1989–2002 processes will load the
updated option 1 runner. Confirm their generated namelists/acceptance reports
when they start. Existing retro files retain their historical provenance;
switching restart options does not remove inherited state errors.

Commissioning sequence:

1. Repeat the isolated October 1–3 daily checkpoint/replay test with option 1.
2. Run isolated spring (March 2–May 1) and summer (July 1–September 1) 2026
   monthly-restart comparisons, option 0 versus 1, from identical frozen initial
   pairs and identical forcing. Report active-cell endpoint soil/routing state,
   runoff-depth differences, daily streamflow differences, monthly boundary-jump
   differences and per-reach integrated flow differences. These are sensitivity
   tests, not observational validation; do not sum connected-reach volumes.
3. Refresh CNRFC NRT from the inherited March 2 retro checkpoint using option 1,
   staged acceptance and recoverable transaction backups. The runner's
   `--refresh-from-retro --acceptance-report <passed-test-cycle.json>` requires
   a successful option-1 daily replay test in addition to SLURM dependencies.
   The refresh creates daily checkpoints for commissioning; retention remains
   plan-only. No old canonical files are changed before staged acceptance.
4. Review refresh acceptance/publication before enabling the daily operations
   hook. The hook remains disabled during commissioning. Keep the old retro
   history and restart files; record the option transition for future calibration.

Seasonal comparisons use `slurm/test_cnrfc_seasonal_restarts.sh spring|summer`;
reports live under `nwm/runs/cnrfc/nrt/seasonal-restart-tests/job-<id>/report.json`.
They never publish their model products to canonical output/restart paths.

Submitted transition jobs (64 MPI ranks each, ordinary shared-128 resources):

| Job | Purpose | Gate |
| --- | --- | --- |
| 4812583 | Corrected October 1–3 daily replay acceptance | Isolated test |
| 4812584 | Spring monthly restart sensitivity | After successful 4812583 |
| 4812585 | Summer monthly restart sensitivity | After successful 4812583 |
| 4812586 | March 2–October 10 NRT refresh | After all three succeed, plus replay report gate |

Spring/summer jobs run independently in private workspaces. The refresh waits
for both to finish so initial canonical restart snapshots cannot race with its
publication. Its six-hour limit is a commissioning allowance (old catch-up was
2h15m); it is not a change to routine daily job limits. Full suite after these
changes: 513 tests passed. Submission is not model/scientific acceptance; review
the job-specific reports before enabling routine operation.

### October-only recovery of the initial refresh

Jobs 4812583–4812585 completed. The corrected daily continuation passed;
spring/summer sensitivity results are retained separately. Job 4812586 accepted
213 days (March 2 through October 1 00 UTC), but rejected its October 1–10
segment after the model finished because canonical forcing changed during that
segment. No canonical model publication occurred.

Use `--refresh-from-retro --resume-workspace <failed-cycle-workspace>` together
with the passed `--acceptance-report` and the original endpoint to resume this
case. Recovery requires an unpublished failed option-1 refresh, a contiguous
accepted prefix, unchanged prefix forcing identities, and all expected staged
outputs/daily restart pairs. It preserves the original workspace and hardlinks
only accepted-prefix products into a new job workspace. October begins from
the accepted October 1 checkpoint, not the old canonical option-0 checkpoint.

The remaining forcing collections and validated subset receipts are pinned in
`forcing-snapshot/` in the new workspace. CNRFC subsetting atomically replaces
published files; hardlinks retain the selected versions when new revisions
arrive. Capture verifies identities and the model verifies the pinned inputs
before/after simulation. Any later canonical revisions are explicitly listed
in `source_revisions_after_snapshot` for the next rolling replay; they do not
invalidate an internally consistent snapshot run. This is not an in-place
editing mechanism: never modify pinned input/output files in place.

After the remaining segment passes, the existing transactional publisher merges
calendar-day boundaries and publishes the complete refresh with backups. The
original failed report, accepted products and prior canonical versions remain
recoverable. No retention cleanup or routine scheduling is enabled by recovery.

Recovery job **4821570** uses 64 MPI ranks, 120 GB scratch and a two-hour limit
on ordinary shared-128 resources. It reuses job 4812586's March–September
prefix and reruns October 1–10. Reports and the new publication journal are
under `nwm/runs/cnrfc/nrt/cycles/job-4821570/`; submission alone is not acceptance.

Job 4821570 subsequently completed in 32m21s and published the complete
March 2–October 10 refresh. All 1,334 target identities matched the transaction
journal when reviewed. The October model integration took 5m26s; segment
acceptance finished in 6m32s. This successful publication, together with the
daily restart-continuation test, allowed activation of the daily dependent hook.
No separate model cron row was installed. Restart/staging/backup retention
remains plan-only; enabling daily simulation does not authorize data deletion.
