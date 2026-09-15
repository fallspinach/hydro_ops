# Multi-day precipitation remapping pilot

Job **4521679** runs `bin/benchmark_precipitation_multiday.py` for April 11–13,
2026, covering the previously inspected wet CNRFC day. No running production
code or published forcing is changed. The allocation is 64 CPUs, 240 GB node
scratch, and an eight-hour limit; library threading is pinned to one.

The same precipitation routine and precomputed weights are used in three rounds:

1. Three daily 30-hour batches, one precipitation-product remap worker.
2. Three daily 30-hour batches, two remap workers.
3. One 78-hour batch, two remap workers.

The third round eliminates twelve duplicated halo hours and reduces remap
invocations per product from three to one when availability is unchanged.
Hourly resolution, source selection, missing-data handling, MRMS quality checks,
and CNRFC six-hour reconciliation are not intentionally changed.

All variables in each of the 72 owned hourly precipitation files (including
source IDs, QC, coordinates and time) are compared by exact data hashes against
the daily reference. Outer halo files are not compared because their six-hour
blocks can be incomplete. Input file identities must remain unchanged across
the rounds. Output file bytes and batch metadata need not be identical.

Reports: `forcing/work/precipitation-multiday-benchmark/job_4521679/summary.json`.
Log: `forcing/logs/precip-multiday-benchmark-4521679.out`.
Scratch-only output/intermediate files are removed after each batch is measured
and fingerprinted; only machine-readable reports remain permanently.

Processing timings include native preparation, CDO remapping, hourly compositing,
writing, and six-hour reconciliation. Individual CDO subprocess elapsed times
are recorded separately; with parallel workers their sum is not wall time.
Hash verification is timed separately and excluded from processing comparisons.
This is a single sequential trial per configuration, so cache effects and changing
cluster load can influence results. No throughput improvement is assumed before
measurement; correctness differences fail the benchmark and require investigation.

Success would justify a subsequent integrated baseline/PRISM repair pilot, not
automatic deployment. Cross-task halo caches, production scratch budgeting and
source-fingerprint invalidation remain separate integration work.

## Isolated full production benchmark

The precipitation-only test passed all 72 hourly comparisons. Processing times
were 1,868 seconds (daily/one worker), 1,293 seconds (daily/two workers), and
848 seconds (78-hour batch/two workers). CDO calls decreased from 21 to seven.

Paired seven-day repair runs now cover September 1–7, 2021, using stable inputs:
reference **4521784**, optimized **4521785**, exact comparison **4521786**.
Each repair run reserves 64 CPUs and 240 GB scratch. Dependencies serialize the
reference and optimized runs; the comparison follows successful completion of both.
Reports and isolated outputs are under
`forcing/work/post2020-production-benchmark-20260913T154753/`.

Both runs rebuild nine baseline calendar days, apply PRISM constraints, assemble
seven calendar-day files, repair the domain, mask, checksum and publish to the
benchmark directory only. The optimized path prepares job-local precipitation
caches in groups of at most three baseline days with two product-remap workers.
Each baseline day checks source, static-asset, and cached-output identities before
reusing precipitation. The cache is removed after its group is consumed, bounding
scratch requirements. Native grids, weights, hourly resolution and source policies
are unchanged. Cache settings or identities that differ cause a failure, not reuse.

The optimized path also opts into the fast static-mask writer. Fresh embedded v3
domain-repair audits permit chunk-integrity checks; unknown audits and each month's
first day retain full read-back. Existing campaigns do not set either benchmark
flag and retain their existing behavior. Benchmark flags require output beneath
`forcing/work` and prohibit the NRT stream (which would otherwise publish baseline
data outside the benchmark). No automatic activation is attached to this test.

The final comparison checks every variable's values for all 168 hours, including
source/QC fields, time and coordinates, and checks PRISM/CNRFC/domain policies.
Invocation: `bin/benchmark_post2020_production.py`; its submission journal records
all three job IDs. End-to-end elapsed times are available from SLURM accounting.
This compares the combined optimization, not an isolated estimate of each change.
