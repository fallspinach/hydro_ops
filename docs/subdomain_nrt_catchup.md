# Expanded CNRFC and Yampa NRT catch-up

The B120 and Yampa NRT catch-up outputs have since been promoted, and both
domains passed daily replay acceptance and are enabled in the
[daily model workflow](subdomain_daily_model_operations.md). References below
to isolated paths or disabled hooks describe the original catch-up campaigns.

## B120 continuation

`bin/submit_subdomain_nrt_catchup.py --domain b120` submits a separate campaign
under `nwm/status/b120-nrt-catchup/`. It starts at the accepted B120 retro
endpoint (April 18, 2026 00 UTC), using its own paired restart, and ends at
midnight of the newest available canonical CNRFC NRT forcing collection.
It refreshes B120 hourly and available daily/monthly forcing from CNRFC,
preserving the established parent provenance, before a two-day continuous and
resumed warm-start test. Production depends on that test passing.

Both model stages use 32 MPI ranks, monthly and terminal paired checkpoints,
daily LDASOUT and hourly/daily CHRTOUT. Output and restart publication initially
uses `b120/nrt/nrt_continuation_v1/`; routine daily operations are not enabled.
The immutable plan pins the endpoint and predecessor identities, and the gate
pins the full forcing interval before simulation. This follows the same
`rst_typ=1` acceptance and record-stacking checks described below.

The initial B120 submission targets April 18–October 30, 2026 at 00 UTC:
forcing refresh **5068494**, dependent restart gate **5068495**, and dependent
production **5068496**. Submission is not execution or publication acceptance;
inspect the job-specific reports under `nwm/status/b120-nrt-catchup/b120/`.

## Yampa publication promotion

Yampa's accepted April 18–October 29, 2026 NRT campaign is promoted with
`bin/promote_yampa_nrt.py --execute` after its audit-only invocation passes.
The standard published locations are:

```text
nwm/outputs/yampa/nrt/{hourly,daily}/YYYY/MM/
nwm/restarts/yampa/nrt/YYYY/MM/
```

Runs retain the `nrt_continuation_v1` campaign name. The promotion journal and
file-identity plan live under `nwm/status/yampa/nrt-promotion/`; rewritten JSON
originals are preserved there. Promotion takes the model stream lock, checks
the queue, requires accepted hourly archive receipts and continuous monthly
acceptance, and verifies the unchanged data identities after directory renames.
Future runner calls select the standard paths only after the journal is complete;
an incomplete promotion blocks publication. This does not enable daily Yampa
operations. The catch-up paths below describe the original campaign submission.

The user authorized NRT continuation after both retro campaigns passed through
**2026-04-18 00 UTC**. Continue from each grid's own accepted paired restart at
that instant, not a recycled CONUS state or the older original-CNRFC checkpoint.
Do not rerun the March–April period already accepted in retro. The new NRT model
stream begins April 18, although NRT forcing archives begin March 2.

## Dependency chain and resources

```bash
source bin/project_environment.sh
"$HYDRO_OPS_PYTHON" bin/submit_subdomain_nrt_catchup.py
```

The submitter selects the newest CONUS NRT collection's **00 UTC** as a fixed
endpoint and verifies that hour exists. The initial target is **October 29,
2026 at 00 UTC**; later hours of that partial forcing day are deferred. This
bounded implementation requires a same-year interval and at least two days.
It is not a cross-year routine operations planner.

1. Crop CONUS NRT hourly and available daily/monthly summaries to the approved
   **expanded CNRFC** (`cnrfc_hu18_v1`) masks. Do not overwrite original `cnrfc`.
2. Refresh CONUS-derived UCRB NRT subsets, then refresh **UCRB-derived Yampa**
   using `afterok`. Preserve existing parent provenance rather than switching
   Yampa's parent silently.
3. After each domain's job-specific forcing report passes, pin its forcing
   inodes and run an April 18–20 warm start, plus April 19–20 continuation.
   Compare eight land/routing state fields with the uninterrupted endpoint.
4. After that matching handoff gate passes, run production April 18 through
   the fixed endpoint, with monthly checkpoints and the terminal partial month.

Forcing synchronization uses 64 CPUs, 16 workers, 120 GB scratch and an eight-hour
limit. Model jobs use **64 MPI ranks for expanded CNRFC**, **32 for Yampa**, 120 GB
scratch and six hours. Jobs use ordinary `shared-128`; never NRTRES for `mpan`.
The domains' model chains run independently. Existing model/forcing campaigns
and compiler/MPI runtime setup are preserved.

## Acceptance, snapshots and products

`bin/sync_subdomain_nrt_forcing.py` reuses existing crop audits and bounded
source-race retries, including each destination's established writer lock.
Unchanged accepted subsets are skipped. Missing interior source days, terminal
collections or accepted parent/output receipts fail closed. Available summaries
are cropped, not recomputed; no remapping or new filling is introduced.

`bin/run_subdomain_nrt_catchup.py` requires parameter/geometry hashes, accepted
retro predecessors and exact restart identities. The handoff tests validate
coordinates, active-cell forcing, daily reductions, all-variable hourly record
stacking, unchanged forcing and paired restarts, and compare continuous versus
resumed state (rtol 1e-5, atol 1e-6). Production requires the matching domain,
plan and mask gate and unchanged initial checkpoint checksums.

Snapshots use hardlinks in the run directory, with validated receipts and inode
identities through the endpoint collection. Atomic source publication can replace
live files without altering pinned inputs. The filesystem must support these
hardlinks. Existing/partial snapshots and submission journals require inspection
before retry; the submitter refuses duplicates. Snapshots and originals remain
recoverable; no cleanup is authorized by this catch-up.

The established monthly model runner permits only these two isolated domains'
explicitly gated `nrt_continuation_v1` combination as new NRT paths. Retro and
old-CNRFC behavior remain unchanged. Model settings are `rst_typ=1`,
`RSTRT_SWC=0`, `GW_RESTART=1`, `t0OutputFlag=0`, `PCP_PARTITION_OPTION=1`,
600/600-second routing and lakes off. Retain daily LDASOUT, hourly/daily CHRTOUT,
calendar-day stacking of hourly output, monthly paired 00 UTC checkpoints and
the terminal checkpoint. The two-day tests publish only to isolated test paths;
production replays those days in its own namespace.

```text
forcing/outputs/{cnrfc_hu18_v1,ucrb,yampa}/nrt/{hourly,daily,monthly}/...
forcing/status/<domain>/nrt-catchup-<forcing-job>/
nwm/{runs,outputs,restarts}/{cnrfc_hu18_v1,yampa}/nrt/nrt_continuation_v1/
nwm/status/subdomain-nrt-catchup/
  plan.json, submission.json
  <domain>/handoff-acceptance.json
  <domain>/{gate,production}-<job>.json
```

The plan pins predecessors, masks, endpoint and job-specific forcing reports.
Inspect the handoff gate, monthly acceptance and final interval marker:
`COMPLETED` alone is not publication acceptance or scientific validation.
This does **not** migrate original-CNRFC products, switch operational masks,
change cron, or enable routine NRT modeling for the expanded domain or Yampa.
Those steps follow review of this catch-up.

## Initial submission — October 29, 2026

| Stage | Expanded CNRFC | Yampa |
| --- | --- | --- |
| NRT forcing refresh | 5063627 | 5063629 (after UCRB 5063628) |
| Two-day handoff/continuation gate | 5063630 | 5063632 |
| April 18–October 29 NRT model | 5063631 | 5063633 |

Both direct-CONUS forcing jobs started with 64 allocated CPUs, 125 GB allocated
memory and 120,000 MB requested scratch. Their plans each cover 489 available
NRT hourly/summary files. Initial reports showed no failures; this does not yet
establish completed publication. Fourteen focused tests and the full suite
(**570 passed, three skipped**), Ruff, shell syntax and diff checks passed before
submission. See the job-specific forcing reports and gate/production reports for
subsequent execution and acceptance rather than treating this submission table
as evidence of completion.
