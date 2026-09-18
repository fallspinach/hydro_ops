# Detailed post-2020 repair timing study

The paired production benchmark passed exact comparisons but improved only from
5:43:20 to 5:24:30 for seven published days (5.5%). Stage-only improvements are
therefore insufficient evidence for large campaign speedups.

## Profile job 4523052

`slurm/profile_post2020_day.py` runs the reference repair path for September 4,
2021, including three boundary-support baseline days and two PRISM windows.
It reserves 64 CPUs and 240 GB scratch, writes isolated results under
`forcing/work/post2020-stage-profile/job_4523052/`, and never replaces production
files. Logs: `forcing/logs/post2020-stage-profile-4523052.out`.

The profiling launcher adds `tools/forcing_profile` to PYTHONPATH only for its
child workflow and sets `HYDRO_OPS_PROFILE_DIRECTORY`. Its `sitecustomize.py`
records cProfile data and subprocess wall times for normally exiting Python
interpreters. Per-process `.pstats` and JSON files are saved under `profiles/`.
The profiler is absent from ordinary production environments. A subprocess test
verifies that parent and child reports are both generated.

Interpretation cautions:

- Instrumentation adds overhead; use prior uninstrumented runs for throughput.
- Parent cumulative times include children waited on; do not sum overlapping
  parent/child cumulative times to estimate wall time.
- Main-thread cProfile does not measure every worker thread/process's internals;
  process boundaries and blocking times still identify expensive pipeline stages.
- One-day setup/halo overhead is proportionally larger than a seven-day batch.

## Code-inspection leads, not measured conclusions

1. `ConservativeOperator.apply` and `backproject_ratio` rebuild link-validity masks
   and weighted denominators using `bincount` on every iteration. The 80-iteration
   solve may benefit from reusing denominators where finite-link masks are provably
   unchanged. Any fast path needs a nonfinite-data fallback and numerical comparison.
2. Reconciliation stores 24 full-resolution float64 hourly depths and creates more
   full-period arrays (stacked inputs, fractions, corrected depths). Memory traffic
   and allocation may matter even when CPU availability is high.
3. Temperature constraints and reconstructed coupled variables are followed by a
   compressed PRISM-window archive, calendar-day archive, domain-repair pass and
   static-mask pass. Reducing complete-file rewrites may outperform remap tuning.
4. Domain repair runs a full completeness scan before even checking whether a
   file is already certified. Later masking also performs checks. Any audit reuse
   must be tied to the actual validated candidate and limited to unchanged values.

Next decisions should use the profile's self times, subprocess durations and
archive/repair costs. Do not reduce iteration limits, loosen acceptance criteria,
or skip audits on newly calculated fields merely to improve runtime. All source
physics and production settings remain unchanged by this study.

## Findings and next benchmark

Job 4523052 passed in 1:33:12. Per PRISM window, numerical precipitation
reconciliation cost about 61 seconds, coupled temperature overrides 87 seconds,
and archive creation 232 seconds. The complete window cost 437 seconds. Calendar
assembly added 234 seconds, final repair 178 seconds, and the old masking path
365 seconds. The three supporting baseline archives each cost approximately
four minutes, and their repairs about five minutes. These measurements prioritize
fewer complete archive passes over changing the 80-iteration solver.

Benchmark **4523479** tests only the first candidate: lossless compressed-chunk
calendar assembly. `bin/benchmark_chunk_calendar.py` prepares two 12–11 UTC
windows from validated September 3–5 calendar outputs, then assembles September 4
using both the current targeted-verification writer and experimental raw-chunk
writer. Preparation is outside the timing comparison. Both timed writers transfer
to permanent benchmark storage. The new writer verifies every copied chunk and
the transfer checksum; a separate full decoded comparison checks all fields,
coordinates, timestamps, and variable attributes against the current writer and
original calendar file.

The experimental `forcing.chunk_archive.assemble` has no production callers and
does not support value overrides. It rejects incompatible compression/chunking
and extrema metadata needing recalculation, checks source identities and static
values, and only writes new destinations. Static variables with differing chunk
layouts use value copying rather than raw copying. This is not yet the complete
production manifest/metadata publication implementation.

Results: `forcing/work/chunk-calendar-benchmark/job_4523479/summary.json`.
Log: `forcing/logs/chunk-calendar-benchmark-4523479.out`.
Allocation: 12 CPUs, 120 GB scratch, two-hour limit. Existing production remains
unchanged. Combined repair/masking and PRISM override-aware chunk copying are
subsequent experiments, not changes included in this benchmark.

## Opt-in archive integration and seven-day comparison

4523479 passed all decoded comparisons: current assembly 232.47 seconds versus
40.47 seconds for chunk assembly, including integrity checks and transfer.

`create_daily_archive(..., chunk_copy=True)` now preserves the archive manifest
and global attributes through the chunk path. Incompatible encodings/extrema
metadata fall back to the original writer; integrity errors do not. Corrected
time-variable overrides are written through NetCDF and every corrected record is
read back, while unchanged chunks retain their compressed bytes and receive
raw-chunk verification. Transfers are checksum-verified before replacement.
The default remains the existing writer.

Calendar materialization and PRISM reconciliation opt in only with
`HYDRO_OPS_ARCHIVE_CHUNKS=1`. The rebuild controller restricts this experiment
to isolated retro output beneath `forcing/work`; live campaigns are unchanged.
The subsequent calendar metadata cleanup and domain/static audits remain intact.

Paired seven-day benchmark, September 1–7, 2021:

- Reference **4523533**, optimized **4523534**, exact comparison **4523535**.
- Both repair jobs run concurrently with 64 CPUs and 240 GB scratch each.
- Outputs: `forcing/work/post2020-production-benchmark-20260914T055016/`.
- Both use the same daily remapping, domain repair and original static masking.
  Only calendar and PRISM archive writing differ, isolating these two changes.
- The comparison depends on successful completion of both jobs and checks all
  variables across 168 hourly records plus PRISM/CNRFC/domain-policy metadata.
- Fifteen focused tests pass, including corrected-value overrides, missing values,
  manifest publication, and incompatible-time-chunk fallback.

No automatic campaign switch is configured. Combined repair/masking remains a
later experiment. Concurrent storage load can affect the measured speedup.

## Combined writer/mask follow-up (September 16)

The archives-only pair finished in 5:43:33 (reference) and 5:41:19 (optimized),
and audit 4523535 passed. This small end-to-end gain is why improvements measured
on pre-existing 2003 baselines should not be extrapolated to full CNRFC rebuilds.

`bin/benchmark_post2020_production.py --all-optimizations --parallel` repeats
September 1–7, 2021 with nine freshly rebuilt support/baseline days in each arm.
Reference retains conventional PRISM/calendar writers and full static masking;
optimized enables chunk archive writing and fast static masking together.
Both retain identical daily remapping, CNRFC policy, active-cell repair, PRISM
science, scratch publication, and permanent-transfer checks. The multiday remap
cache experiment is explicitly disabled. Month-start masking still receives a
full read-back even in the optimized arm.

Each arm requests 64 CPUs, 240 GB scratch and a 24-hour limit. Outputs remain
under a new isolated `forcing/work/post2020-production-benchmark-*` directory.
No task in 4520189 is cancelled or changed; no automatic production switch is
scheduled. The comparison job waits for both arms and checks all variables,
all 168 hourly records, variable metadata, CNRFC/policy metadata and permanent
publication identities; it also verifies optimized calendar and mask execution.

Both arms opt into the existing interpreter timing hook with
`HYDRO_OPS_PROFILE_TIMING_ONLY=1`: subprocess and process wall times are recorded
without enabling cProfile or writing pstats files. Per-script totals appear in
`acceptance.json`; detailed records live under `timings/reference` and
`timings/optimized`. Parent times include child time: never sum nested scripts.
The top-level rebuild time includes final publication; the independent full
comparison is outside the measured build interval. Timing instrumentation is
absent from ordinary production environments.

Submitted pair: reference **4546585**, optimized **4546586**, dependent full
comparison **4546587**. Results directory:
`forcing/work/post2020-production-benchmark-20260916T163951/`.
Twenty-five focused tests passed before submission.
# Production writer handoff (2026-09-16)

The seven-day, all-three writer benchmark passed: reference `4546585`
took 5:47:21; optimized `4546586` took 4:53:54; audit `4546587`
confirmed bitwise-equal decoded fields, variable attributes, required policy
metadata, and publication identities. This is 15.4% less elapsed time at the
same resources, not a reduction in baseline remapping work.

Replacement array **4549027** takes over only original pending indices
178–307 from **4520189**. Its 103 retro batches use `validated_chunks_v1`
(chunk-aware PRISM/calendar writing and fast static masking); its 27 NRT
batches retain `reference`, because this benchmark covered retro only.
Multiday precipitation remapping remains disabled. Month-start mask checks
retain full validation and permanent publication retains checksum verification.

Original running and completed batches were preserved. The replacement uses
`afterany:4520189` (not `afterok`, because pending originals were canceled),
eight workers, 64 CPUs and 240000 MB reserved scratch per worker, and 48-hour
task limits. Maximum concurrent allocation remains 512 CPUs. It is normal for
the new array to wait for the original workers to drain.

The frozen inventory, benchmark acceptance, original/canceled task states,
submission command, and replacement job ID are recorded in
`forcing/work/post2020-writer-upgrade-20260917T014314249761/`.
`bin/upgrade_pending_post2020.py` implements this pending-only handoff with a
dry-run default and a journal-resume option. Scheduler accounting must expand
array tasks (`sacct --array`), including compressed canceled ranges. A recorded
submission attempt must be reconciled with SLURM before any resubmission.
