# Latest-hour NRT publication — staged rollout

The operational objective is the latest contiguous usable hour, not the arrival
of PRISM, MRMS pass 2, or a complete calendar day. Required northern GFS coverage
and full active-cell validation are unchanged. Files remain grouped by UTC
calendar date; a partial file contains only 00 through its accepted last hour.
No future records are synthesized.

## Implemented, not enabled in cron

`RecentNrt.produce_day(day, end_hour=H, latest=True)` publishes an unconstrained
partial/calendar-day file using the existing atomic transfer and receipts.
The default call retains the existing full-day PRISM workflow.

- Baseline batching, native-donor repair, GFS publication, and final readback
  accept an explicit record count.
- Latest-hour precipitation uses the preceding five-hour halo but does not
  request future hours. Complete available six-hour Stage-IV blocks may constrain
  CNRFC precipitation; incomplete blocks keep the non-Stage-IV composite.
- Mixed primary sources share the bounded precipitation remapping cache.
- Files declare `calendar_day_complete`; receipts record `complete_day` and
  `latest_model_ready_hour`. The latter certifies this file only, not continuity
  across an entire archive.
- Extension rebuilds the available prefix and atomically replaces the file.
  This first implementation is not an append-only writer.
- Truncation, NLDAS-to-HRRR downgrade, and replacement of a PRISM-constrained
  final with an unconstrained final are refused.
- Unchanged input fingerprints reuse the existing publication.

## Validation and activation gates

`slurm/test_nrt_partial_publication.sh` reserves one 128-CPU node and 240000 MB
scratch. It retains the validated 8 assembly / 4 precipitation worker settings;
doubling allocation is not a claim of doubled computational parallelism.
The private historical replay extends September 20, 2026 from 13 to 16 records,
crossing into HRRR/GFS hours, then tests no-op reuse and truncation rejection.
Receipts are under `forcing/work/nrt-partial-publication-JOB/acceptance.json`.
Initial submitted test: **4631499**. Submission is not acceptance.

Test 4631499 passed: 13-hour publication 503.96 seconds, extension to 16 hours
625.71 seconds, unchanged repeat 0.74 seconds, and truncation rejection passed.
The extension rebuilt the complete prefix. No live-cycle latency claim is made.

Follow-up tests submitted September 24 (results pending):

| Job | Gate | Resources |
| --- | --- | --- |
| 4631570 | Extend accepted partial replay to 24 hours, apply PRISM, repeat, reject PRISM downgrade | 128 CPUs |
| 4631571 | Replace partial HRRR/GFS with NLDAS, repeat, reject primary downgrade | 128 CPUs |
| 4631572 | CONUS cold-start model reads the accepted 16-record file for a 15-hour run; audit restart | 120 MPI ranks |
| 4631573 | Discover/download one recent HRRR analysis/previous-cycle precipitation pair and matching GFS bundle | 4 CPUs |

All test paths are private. The model smoke uses an isolated scratch forcing
root containing only the partial file, so a full production archive cannot hide
a partial-reader problem. The live acquisition test probes up to seven recent
hours, treating only HTTP 404 as unpublished. It validates one source hour, not
continuous model-ready coverage or a complete cron cycle. Publication tests keep
the established worker counts; larger allocations do not themselves double
worker parallelism.

Live acquisition **4631573 passed**: at 17:09 UTC September 24 it downloaded the
16 UTC HRRR hour and matching 12 UTC GFS cycle/f004. The 17 UTC HRRR pair was
not yet published. This confirms hourly acquisition works without waiting for
day-end, but the operational scheduler integration remains gated.

## Follow-up performance and coverage work

- **4635990** repairs only April 12 and September 1–13, 2026 NRT files using the
  approved static envelope. It holds the NRT cycle and summary-controller locks,
  stages on scratch, verifies all records/variables against original retained
  values, and uses checksum-verified atomic transfer with durable mask audits.
  No PRISM recalculation or remapping is needed. Report:
  `forcing/work/nrt-static-mask-14-4635990/acceptance.json`.
- **4635991** compares assembly/remap/repair workers 8/4/4 against 16/8/8 on a
  128-CPU allocation, with reference runs before and after the doubled trial.
  Source fingerprints and all output variables must match. This measures worker
  scaling, not a guarantee of 2x speedup or distributed multi-node execution.
  Report: `forcing/work/nrt-doubled-workers-4635991/acceptance.json`.

Benchmark **4635991 passed**: reference runs 975.23 and 949.91 seconds, doubled
workers 841.78 seconds, or 12.55% less time against their mean. All compared
variables, masks and metadata matched. NRT defaults now use **16/8/8**, and new
recent-NRT scheduler submissions request **128 CPUs** with 240000 MB scratch.
Already queued/running jobs are not resized. Rollback is 8/4/4 with 64 CPUs.
Worker changes do not invalidate baseline scientific fingerprints.

## Operational rollout checks

- **4636052 passed** the previously blocked restart audit, explicitly expecting
  `2026-09-20_15:00:00`. All seven audited land-state fields contain no missing
  active values across 10,315,371 active cells, including 501,663 northern-gap
  cells. The original 15-hour model simulation need not be rerun. Its wrapper
  now supplies the correct expected timestamp.
- **4636053** tests live HRRR/GFS acquisition through the latest available hour,
  including the preceding five-hour precipitation halo, followed by private
  publication using 16/8/8 workers and an unchanged repeat. Available local
  preferred products remain eligible; this test does not refresh every optional
  input source or install a cron schedule. Receipt:
  `forcing/work/nrt-live-hour-4636053/acceptance.json` (pending).
- The summary command's two-month revision test passed: changing February 1
  hourly endpoints republishes January 31 and February 1 daily summaries, then
  January and February monthly summaries, leaving all unrelated files unchanged.
  This is a small-grid integration fixture; prior CONUS summary jobs test scale.

The live test and subsequent scheduler integration remain separate activation
gates. These checks do not claim that latest-hour production is already in cron.

Still required before activation: complete-day transition, PRISM/NLDAS revision
handoff, model-reader test, current-hour acquisition (including partial HRRR),
and connection of the readiness planner to a locked operational extension worker.
Cron's existing end-date cutoff is unchanged. The separate revision cycle will
inspect approximately 11 days; latest-hour extension must run ahead of revisions.
Neither a source-ready plan nor a submitted test is a model-readiness claim.
