# Isolated 2003 PRISM publication optimization benchmark

`bin/benchmark_retro_prism_optimization.py --submit` submits two paired tests:
January 15–16 and July 15–16, 2003. Each reference/optimized arm gets 12 CPUs,
240 GB scratch, and a six-hour limit. Four arms may run concurrently (48 CPUs
total); an independent full decoded comparison waits for all four to succeed.
Concurrent storage load and cache effects remain potential timing confounders.

Both arms reuse existing baseline files, calculate three stable PRISM windows
(including boundary support), publish two 00–23 UTC days, perform active-cell
repair, apply the v4 static envelope, and use checksum-verified atomic transfer.
All files go beneath a unique `forcing/work/retro-prism-optimization-*` directory,
never into operational forcing streams. Existing benchmark jobs and the larger
campaign's acceptance gate are unchanged. Baselines are not deleted.

Only these two mechanisms differ:

- Calendar materialization: reference NetCDF rewrite versus compressed-chunk
  copying of unchanged values, with integrity verification.
- Static masking: full reference rewrite/read-back versus the previously tested
  chunk masker and integrity verification, using the immediately preceding
  active-cell repair's embedded content audit. Unknown audits still fall back to
  full read-back. The chosen dates are not month starts, which force full reads.

PRISM-window writing remains the reference path in **both** arms, including
normalization of missing legacy precipitation timing provenance. No numerical
solver, remapping, source preference, or acceptance tolerance changes.

Each arm records `timing.json`: subprocess durations for PRISM windows, calendar
assembly, active-cell repair, and masking plus final transfer. Whole-trial elapsed
time includes support-window initialization and publication checks; do not count
it as baseline-generation throughput or simply sum overlapping nested timers.
The first PRISM-window event isolates initialization overhead. Mask journals
contain finer write/verification timings. Calendar events record the actual
archive writer so silent fallback cannot be mistaken for successful optimization.

The dependent audit checks every decoded value (including QC/provenance and
coordinates), variable metadata, scientific policy metadata, and final publication
identities. It verifies that both intended fast paths actually ran and writes
`acceptance.json` with per-season speedups. Full comparison is outside the timed
production interval. Passing does **not** automatically switch live production.

## Submitted trial

Results: `forcing/work/retro-prism-optimization-20260915T015927/`.

| Dates | Reference | Optimized |
| --- | --- | --- |
| 2003-01-15–16 | 4526247 | 4526248 |
| 2003-07-15–16 | 4526249 | 4526250 |

Full comparison: **4526251**, dependent on all four arms succeeding.

## PRISM-window writer follow-up

`bin/benchmark_retro_prism_optimization.py --submit --window-writing` repeats
the same four trial arms in a new `retro-prism-window-optimization-*` directory.
Both arms now use the validated fast calendar assembly and static masking. Only
the optimized arm enables compressed-chunk copying during PRISM-window writing.
The original paired trial is complete and its output/report files are unchanged.

The archive writer supports normalized legacy precipitation timing provenance:
missing `precip_timing_source_id` records become zero (unknown), and existing
records are preserved. This categorical field is written through NetCDF and fully
verified; unchanged compatible variables retain their original compressed chunks.
The four corrected fields (RAINRATE, T2D, Q2D, LWDOWN) are written normally and
every corrected record receives read-back verification. No scientific tolerances,
calculation algorithms, coverage policy, or donor selection change. Unsupported
encodings still fall back to the reference writer, and the benchmark acceptance
check rejects fallback when assessing successful fast-path execution.

Both writers report archive writing, verification, and total elapsed seconds in
their manifests. The harness captures those timings for each PRISM window and
calendar assembly. This distinguishes writing savings from the precipitation
solver and temperature/humidity/longwave calculations. Results remain isolated;
passing does not automatically enable the window optimization in production.

Window-writing results directory:
`forcing/work/retro-prism-window-optimization-20260915T031946/`.
January reference/optimized jobs: **4526336 / 4526337**.
July reference/optimized jobs: **4526338 / 4526339**.
Dependent full comparison: **4526340**. Each trial arm retains the same
12-CPU/240-GB-scratch allocation. Twenty-nine focused tests passed before submission.

## Results and adoption

All four window-writing trials completed, and **4526340** passed full decoded
bitwise comparison of all variables/records in 5:45. January elapsed time fell
from 38:35 to 34:40; July from 41:54 to 37:10 (about 10–11% less time). Archive
writing averaged roughly 215 versus 90 seconds/window; including checks and
transfer, about 265 versus 192 seconds/window. The chunk path verifies all four
corrected fields, not only precipitation, so verification itself is more expensive.

On September 15 the user approved adoption of all three optimizations for the
remaining retro campaign. See `retro_remaining_campaign.md` for the submission-time
handoff and scope. The benchmark reports themselves remain unchanged historical
records (`production_switch=false` records their original non-switching audit).
