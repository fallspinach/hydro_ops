# Incremental NRT operational rollout

## Service objective and lanes

Target verified NRT publication within **30 minutes** of coordinator launch;
**60 minutes is the acceptance ceiling**, including source refresh, dependencies,
queueing, computation, checks and transfer. A fast compute time alone is not a
passing operational benchmark. Do not relax scientific checks or replace good
files with incomplete candidates to meet a deadline.

The proposed schedule has three independently coordinated lanes:

- Fast NRT every six hours, including the daily reconciliation hour: newest
  available hours first, missing outputs next, essential recent revisions next.
- Daily NRT reconciliation: older mutable Stage-IV/MRMS/NLDAS-2/PRISM revisions.
  These remain NRT, not retro. Deferred work must remain a durable backlog.
- Daily retro eligibility scan: promote newly stable days, skip unchanged stable
  outputs, and run without occupying the fast lane's reserved capacity.

Daily retro scanning replaces a monthly scan only after rollout acceptance;
daily checks do not imply that PRISM stable data change daily.

## Implemented foundation (not activated scheduling)

`bin/plan_incremental_forcing.py` emits read-only JSON plans from explicit
source-change events. It orders new data newest-first, routes older revisions
to daily NRT and requires stable eligibility for retro. Its default four-day
recent window is provisional, not a new source acquisition retention policy.
Unknown changes are errors, not silent no-ops.

Example scenario preview:

```bash
python bin/plan_incremental_forcing.py \
  --events config/incremental_forcing_benchmark_events.json \
  --as-of 2026-08-26T02:00:00Z --lane fast
```

The scenario file contains independent cases, not a deduplicated real cycle.
The planner deliberately has `execution_enabled=false`: its reduced task graph
is a specification to benchmark, not yet a replacement numerical implementation.

The existing recent-NRT worker now records per-day elapsed time, coordinator
launch time, worker start and finish, and latency status in its existing JSON
reports under `forcing/status/nrt-gfs/`. Pre-worker time includes refresh and
dependencies as well as queueing; it is not mislabeled as pure queue time.
Legacy/manual calls without a launch timestamp report `launch_time_unknown`.
Timing misses are reported separately from scientific pass/fail; this initial
instrumentation does not kill valid ongoing calculations at 60 minutes.

## Work avoidance rules to implement and verify

1. Stage-IV/MRMS-only revisions rebuild precipitation, its CNRFC six-hour
   constraints and eligible PRISM precipitation reconciliation, not all eight fields.
2. PRISM precipitation-only revisions reuse source remaps and baselines. PRISM
   temperature revisions recompute temperature and coupled humidity/longwave.
3. NLDAS-2 arrival replaces the HRRR/GFS bundle consistently. Source change
   receipts include grids, masks, algorithm versions and input revisions.
4. Share remapped inputs and PRISM windows across adjacent output dates; resolve
   six-hour and 12–12 dependencies once while retaining 00–23 storage grouping.
5. Combine final masking, active-cell repair, validation and calendar publication
   to avoid successive complete-file rewrites. Retain checks on newly computed data.

## Current blockers and acceptance sequence

The current coordinator can wait for older baseline work before recent NRT,
serializes overlapping NRT cycles and defaults to a two-day-lag endpoint. The
current recent worker requires 24 complete hourly records per calendar day.
Therefore **it cannot yet claim six-new-hour, current-day operational delivery**.
No live cron entry, active campaign or production policy is changed by this phase.

Next implement a source-availability cutoff and explicit valid-hour coverage for
partial current-day publication, per-window locks/shared-cache receipts, and a
separate fast submission path that does not depend on older reconciliation.
Partial-day files must expose their coverage and never masquerade as complete
24-record days. Downstream model planning must check the required hour interval.

Benchmark, on isolated real-data destinations: unchanged cycle; six new hours;
Stage-IV-only revision; PRISM-only revision; and NLDAS-2 arrival. Compare values,
source/QC metadata, active coverage and timestamps against the existing full
workflow. Exercise overlapping cycles, failed refresh, missing halo inputs,
delayed allocation and deferred-backlog recovery. Require complete requested
coverage, retained prior publications on failures and launch-to-publication
latency below one hour before promoting a new cron schedule.

The latest historical archive benchmark's 0.7% end-to-end improvement is not
evidence that these NRT objectives have already been met.
