# CHRTOUT record-dimension incident and recovery

## Failure mechanism

The native writer produces one-hour CHRTOUT files with a singleton `time`
coordinate but channel variables such as `streamflow(feature_id)` without a
record dimension. The production publisher passed these files to `ncrcat`.
NCO concatenated `time` but treated channel arrays as non-record variables,
retaining a single array rather than one per hour. A collection containing 24
time values is therefore **not evidence of 24 channel states**.

The prepublication validator checked each original hourly file and output-time
coverage, and the collection validator checked its time coordinate. Neither
required each channel variable to have shape `(time, feature_id)` or compared
all archived records with their individual source arrays. These checks were
insufficient. Earlier archive-acceptance claims are superseded by this incident.

This is an output-publication failure, not evidence that model integration,
land outputs, native daily channel reductions, or restart states are invalid.
It applies to both CONUS and CNRFC campaigns using this publisher.

## Immediate safeguards

- CONUS pending jobs 4524573–4524587 (1988–2002) were placed on user hold.
- SLURM denied suspension of active job 4524572 (1987). Its Python controller
  PID 2231347 on awr-2-32 was stopped with SIGSTOP instead. Its current June
  model segment continues; the stopped controller cannot publish/delete scratch.
- `bin/preserve_conus_hourly_recovery.py` copies completed raw hourly outputs
  with checksum verification while that segment runs. After successful model
  completion it saves all regular scratch files and stops allocation 4524572,
  rather than resuming the unsafe publisher. A copy failure or timeout does not
  cancel the allocation. Inspect its report before any manual intervention.
  After cancellation, year 1988's dependency on 4524572 must be retargeted to a
  successful replacement 1987 completion job; merely releasing the hold would
  leave an unsatisfied `afterok` dependency.
- `publish_hourly` now uses an explicit dimension-promoting publisher instead
  of NCO concatenation, as described below. Malformed multi-hour source files
  are rejected. CONUS jobs remain held pending recovery planning.
- No existing outputs or restart files are deleted or overwritten by the audit.

## Recovery evidence and retention

`bin/audit_chrtout_recovery.py` inventories production archive variable dimensions,
permanent raw hourly candidates, and available restart pairs (matching timestamps
and required state-variable presence; not a full scan of physical values).
It writes `nwm/recovery/chrtout_record_dimension/audit.json`.
The `--restarts-only` option writes `audit-restarts.json` independently so restart
retention need not wait for a full archive-header scan.

`bin/protect_nwm_recovery_restarts.py` retains the audited restart files under
`nwm/recovery/chrtout_record_dimension/protected_restarts/`, with a manifest
`restart-retention.json`. These are hard links, not symlinks: deletion or atomic
replacement of an original pathname does not remove the retained inode. They
consume no second copy of the data. **They are not an independent backup against
disk failure or in-place modification.** Treat both paths as read-only, retain
all pairs, and use new campaign paths for any reruns.

The active-month rescue is recorded under
`nwm/recovery/chrtout_record_dimension/CONUS_4524572_198706/`, including
`recovery.json` with per-file checksums. Preservation in progress is not a claim
that the entire month has been recovered.

## Recovery strategy

### Restart retention audit result

`audit-restarts.json` verified **146 pairs with no header/time/required-variable
errors**. `restart-retention.json` confirms **292 retained files**. This includes:

- 101 CONUS production pairs, every month boundary from 1979-02-01 through
  1987-06-01 inclusive, with no missing months.
- 36 CNRFC production pairs, every month boundary from 1979-02-01 through
  1982-01-01 inclusive, with no missing months.
- Nine additional spin-up, acceptance and initialization pairs, including the
  1979-01-02 initialization needed for the first production interval.

Existing production output trees contain no individually named 12-digit hourly
CHRTOUT files; completed segment runners removed those scratch intermediates.
The full collection-header audit is separate from the restart retention audit.

The completed full audit found **3,073 CONUS** and **1,096 CNRFC** production
collections with feature-only streamflow arrays, and zero with a proper
streamflow time dimension. These counts include terminal midnight-only boundary
files: those one-record files have no additional hourly values to lose, but cannot
repair adjacent multi-hour collections. There were no NetCDF-open errors and no
individually named raw hourly CHRTOUT candidates in the scanned production output
trees. CONUS coverage runs through the 1987-06-01 boundary; CNRFC through
1982-01-01. The audit cannot establish whether an administrator has separate
backups of deleted node-local scratch. The fail-closed guard and existing suite
passed **428 tests** after this incident response.

### Repair sequence

1. Raw hourly files that survive can be republished without model integration.
2. A feature-only collection cannot reconstruct missing hourly states. Previously
   deleted raw files require a separately available backup/snapshot or rerunning
   the affected simulation interval. Do not derive daily means from the malformed
   collections and do not duplicate their single array across 24 timestamps.
3. Existing monthly 00 UTC restart pairs permit independent monthly/yearly reruns.
   Use the original January 2 initialization for the first partial month/year,
   then each saved month-start state. The runs need not be chained across years.
4. Preserve original restart checkpoints and original malformed archives as
   evidence. Write recovery runs to a new campaign, using chronological restart
   times without resetting physical states, counters or accumulations.
5. Enable native daily CHRTOUT during recovery reruns, in addition to the repaired
   hourly collection workflow. Already valid native daily outputs need not be
   recomputed merely to repair hourly storage.

Required publisher tests include distinct values at every hour, correct field
dimensions, exact source-to-archive comparisons for every record, and midnight
merges across monthly/yearly segments. A successful model sentinel or timestamps
alone must never again certify a channel collection.

## Repaired publisher and CNRFC acceptance

`src/hydro_ops/wrf_hydro/channel_archive.py` promotes hourly channel fields to
`(time, feature_id)` while retaining static feature coordinates. It preserves
raw encoded values, fill values and packing metadata, and records initialization
time per record. Before atomic publication, it reopens the candidate and compares
every variable and every record against the original sources. An accompanying
`.archive.json` receipt records the policy and source identities. Source files
must not change during publication. Duplicate hours, time gaps, incompatible
schemas and malformed multi-hour feature-only arrays fail closed.

A retained midnight-only boundary file can be merged with the following 01–23
UTC records. Other existing daily collections are not silently overwritten.
Synthetic tests cover distinct hourly values, packed data, missing values,
static-coordinate mismatches and midnight merging. The full suite passed 430
tests during implementation; subsequent targeted checks also passed.

CNRFC warm-start test **4642075**, February 1–3, 1979, passed on **64 MPI ranks**:
48 hourly records were archived with full source-to-record comparisons and a
real midnight merge, plus two native daily channel records. Model time was
78 seconds; total batch elapsed time was 3 minutes 23 seconds. Raw hourly test
files are retained for inspection. Acceptance report:

`nwm/outputs/cnrfc/retro/tests/archive_recovery_warmstart_19790201_48h/job_4642075/acceptance.json`

The next test is the full 1979 CNRFC recovery run, job **4642078**, using 64 MPI
ranks and the passed short-test gate. Its separate campaign is
`recovery_record_stack_v1` under the usual CNRFC runs, outputs and restarts
directories. Original archives and protected restart checkpoints remain intact.
The full-year run subsequently completed in 3:27:37, with all 12 monthly checks
passed, 8,736 archived hourly records, 365 validated calendar collections,
364 daily land and channel outputs each, and retained monthly restarts. Jobs
4651951 (1980) and 4651952 (1981, after successful 1980) continue recovery.
CONUS production has not been resumed. Failed CNRFC recovery segments retain their raw hourly channel
files on permanent storage for diagnosis rather than relying on node scratch.

## CNRFC retrospective continuation

The approved continuation uses the same validated `recovery_record_stack_v1`
campaign through **2026-03-02 00 UTC**, following the 1981 recovery job. One
64-rank job per year runs in an `afterok` chain, with a partial 2026 job using
`--end-date 2026-03-02`. The last complete simulated day is March 1: March 2's
00 UTC forcing supplies its final interval. The remaining March 2 forcing
cannot support another complete UTC day without March 3 at 00 UTC.

`bin/submit_cnrfc_continuation.py --after-job 4651952` records each submission in
`nwm/runs/cnrfc/retro/recovery_record_stack_v1/continuation-1982-20260302.json`.
The helper refuses to resubmit if that manifest exists. Consult it rather than
rerunning the command blindly. Each successor requires its predecessor to
finish successfully; monthly state/output checks and full hourly publication
comparisons remain enabled. The partial final month also writes a terminal
restart. Existing monthly markers with different intervals are rejected rather
than silently skipping a later extension of a partial month.

Output settings remain daily LDASOUT, hourly and daily CHRTOUT, no lake output,
and monthly restart checkpoints. At approximately 3.4 hours per simulated year,
1982 through early March 2026 requires roughly six to seven days of sequential
runtime, plus queue delays. This does not release the held CONUS chain.

Submission completed: 45 continuation jobs **4652059–4652103**, for 1982–2026,
respectively, chained after 4651952. The full test suite passed 431 tests before
submission-helper coverage was added; the three CNRFC tests then passed,
including a mocked 45-job dependency-chain and duplicate-submission check.
The continuation preflight checked all **16,132** daily forcing files from
1982-01-01 through 2026-03-02 inclusive: every file and subset receipt existed,
with passed status, 24 records and zero reported missing active-cell values.
This is a receipt/coverage check; each monthly run also checks NetCDF time and
variable availability before execution. The original subsetting job's 16
failures were NRT source-change races, not retro forcing failures.

## CONUS recovery launch

CONUS recovery has now been submitted separately from the original held chain:

- **4652105**: January 2–4, 1979 acceptance plus a one-day warm-restart
  continuation (ending January 5). Uses 120 MPI ranks and the repaired publisher.
- **4652106**: 1979 production, with `afterok:4652105` and the application-level
  continuation acceptance gate. Uses 120 ranks, a 48-hour limit, 240 GB requested
  scratch and monthly restart checkpoints.

Both use the new `recovery_record_stack_v1` CONUS campaign, with separate
`acceptance` and `production` output/restart directories. Outputs are daily
LDASOUT, hourly calendar-day CHRTOUT collections and native daily CHRTOUT. The
original CONUS 1988–2002 continuation remains held. Failed CONUS segments now attempt to preserve raw hourly
channel files and any restart files to permanent `failed_raw/<job>/<segment>`
storage before exiting (this cannot protect against abrupt node loss or kill).
Ten publisher/production tests and the production-module lint check passed.

The June 1987 rescue report confirms **720 raw hourly files** and cancellation
of the old allocation only after preservation. These remain available for
republication without rerunning that month; recovery publication is still to do.

### CONUS continuation through 2002 (2026-09-28 UTC)

The corrected 1979 recovery job **4652106** passed in **36h 51m 48s**,
ending at 1980-01-01 00 UTC. Its accepted terminal restart pair was checked
before extending the same campaign through 2002:

```bash
python bin/submit_conus_retro_simulation.py \
  --campaign recovery_record_stack_v1 --extend --completed-predecessor \
  --end-year 2002 --submit
```

Jobs **4665003–4665025** cover **1980–2002**, one year per job, with
`afterok` dependencies between consecutive years. Job 4665003 started immediately;
the rest wait for their predecessor. Each requests 120 MPI ranks, 48 hours and
240 GB scratch, without NRTRES. Daily LDASOUT, hourly and daily CHRTOUT, and
monthly restart checkpoints remain enabled. The old held campaign is untouched.
At the 1979 rate, this sequential chain represents about 35 days of processing,
excluding queue delays and year-to-year runtime variation.

`--completed-predecessor` is only for extending a completed chain whose final
job may have aged out of SLURM's live job table. It requires a successful
`sacct` exit record, the annual acceptance marker, and the expected terminal
restart pair with validated timestamps before omitting that old dependency.
Omit `--submit` to preview; repeat submission is rejected once the requested
end year is already recorded. All 8,402 expected daily forcing paths from
1980-01-01 through 2003-01-01 were present at submission; monthly model preflight
still validates required times and variables. This path check is not a new
full-field forcing audit. Submission and production tests: 12 passed.
