# NRT revision parallelism benchmark

## Motivation

Daily revision job 4652114 processed September 18–24, 2026 serially in about
168 minutes despite allocating 128 CPUs. Saved receipts attribute approximately
100 minutes to baseline reconstruction, 45 minutes to PRISM windows, and 23
minutes to remaining assembly, audits and transfers. Existing 16/8/8 intra-day
workers and optimized writers were already enabled. This is separate from the
successful 22-minute latest-hour extension.

## First controlled experiment

Job **4653406** runs `slurm/benchmark_nrt_revision_pool.sh` and
`bin/benchmark_nrt_revision_pool.py`: serial baseline preparation followed by
two-worker baseline preparation, each with serial PRISM/calendar publication.
Both trials allocate the same 128-CPU node, request 240 GB local scratch and use
a 12-hour batch limit. Production configuration and output trees are unchanged.

The benchmark covers seven output days, September 18–24. It starts with empty
private baselines, including required neighboring days: initially eight unique
baseline dates (September 17–24). Therefore raw total time is not a direct
replay of the previous cycle, which reused September 17. Compare the paired
arms, not the previous job, to estimate speedup.

Baseline dependencies are deduplicated and built once before final production.
The two-process pool uses spawn, private worker scratch, and unique baseline-day
ownership. PRISM and final publication remain serial so overlapping windows
have one writer. In-worker adjacent-window reuse remains enabled; persistent
PRISM caching is disabled in both trials to avoid unequal cache warming or
writing the production window cache. This experiment does **not** yet test
parallel PRISM-window production.

Acceptance requires matching baseline source fingerprints, matching final input
dependencies (excluding artifact SHA differences), and exact comparisons of all
baseline and final variable values, dimensions and variable attributes, plus
selected scientific global metadata. Baselines must not change during final
publication. Source changes between arms invalidate the comparison rather than
silently accepting mismatched workloads. The second arm can benefit from OS
read caching; a reversed-order repeat is advisable before production adoption.

Results and private outputs:

`forcing/work/nrt-revision-pool/job_4653406/acceptance.json`

Each trial also writes `workers-1/timing.json` or `workers-2/timing.json`; the
batch log is `forcing/logs/nrt-revision-pool-4653406.out`. A passed timing trial
alone is not the final acceptance: require the top-level report's `status=passed`
and `exact_values_verified=true`. Original production data are never replaced.

Three unit tests cover dependency deduplication and rejection of baselines that
change during final publication. The benchmark and tests passed lint checks.
No runtime speedup is claimed until the paired job finishes.

## Completed first benchmark

Job 4653406 passed exact baseline and final comparisons with matching source
fingerprints. Serial preparation took 121.8 minutes versus 59.9 with two workers.
PRISM/publication remained 67.4 versus 66.6 minutes. Total processing fell from
189.2 to 126.6 minutes (33% less); the separate comparison audit took 27.4 minutes.
The production configuration was not changed.

## Four-worker staged experiment

`bin/benchmark_nrt_staged_revision.py`, launched by
`slurm/benchmark_nrt_staged_revision.sh`, tests a staged candidate:

1. Four spawned baseline workers, one task per unique required UTC date.
2. Four PRISM-window workers, one task per unique window (not one per output day).
3. Two calendar-day publication workers, using read-only prepared dependencies.

Each window receives exactly its two baseline calendar days and a private work
directory. Signature/identity markers are written only after successful creation
and input-identity checks. A phase barrier precedes publication. Publishers
share the prepared window directory through private-workspace links, but fail
closed on missing/stale windows instead of rebuilding them. Baseline consumption
also refuses stale receipts or changed source/static-asset identities. Default
production behavior is unaffected by this benchmark-only prepared-window flag.

The candidate runs first. The reference then uses the original two-worker
baseline benchmark with its serial production reconciliation/publication path,
not a second instance of the new staged implementation. Both use fresh private
outputs and no persistent PRISM cache. Exact baseline and final-field comparisons,
source-fingerprint agreement, and matching baseline coverage are required.
Six targeted tests cover unique dependency ownership, prepared-input rejection,
and PRISM-window signature compatibility. A 128-CPU node, 300 GB scratch request
and 12-hour limit bound the experiment. This is not production adoption.
The initial 480 GB proposal was rejected by Slurm's allocation test: these nodes
advertise 300 GB scratch maximum. The benchmark reserves that full amount and
samples filesystem free space every 15 seconds, recording its minimum in the
acceptance report. The full suite passed 438 tests before adding this telemetry.

Submitted job **4655305**. Its report is
`forcing/work/nrt-staged-revision/job_4655305/acceptance.json` and its log is
`forcing/logs/nrt-staged-revision-4655305.out`. At submission, production worker
settings were unchanged pending acceptance; activation is recorded below.

## Completed staged benchmark and integration

Job 4655305 passed exact-value and source-fingerprint comparisons. Candidate
baseline/window/publication times were 37.7 / 13.8 / 12.2 minutes, totaling
63.7 minutes versus 124.9 for the two-worker reference (49% reduction).
The separate comparison audit took 26.4 minutes; minimum observed scratch free
space was 138.5 GB. This cold rebuild is still slightly above one hour.

The shared production implementation is `src/hydro_ops/forcing/nrt_staged.py`,
selected through `revision_pipeline` in `config/nrt_gfs.toml`. It initially
remained `serial`; after incremental acceptance, `staged_v1` became the production
default (see activation below). `staged_v1` retains the existing cycle lock, deduplicates dependencies,
checks baseline receipts, skips unchanged final dates, restores dependency-keyed
persistent PRISM windows, and prunes the cache only after parallel users finish.
Window creation uses the same extracted helper as the serial path. Final workers
cannot rebuild their prepared baselines/windows. Progress and phase timings are
written to the usual cycle status JSON. Phase failures produce a failed cycle;
already accepted per-day artifacts remain usable on retry.

`slurm/test_nrt_incremental_staged.sh` exercises this integration on private
copies of the accepted benchmark. Setup first refreshes/accepts a seed and primes
its PRISM cache through the serial workflow. Each timed arm receives cloned
baselines, final outputs and checksum-verified cache entries with rebound path
identities. September 23's private baseline is moved into `withheld/`, and its
private final receipt is invalidated. The candidate uses staged_v1, the reference
uses serial, and both revise the same seven-day window. Acceptance requires:

- Matching inputs and exact baseline/final variable values.
- Exactly one candidate baseline rebuilt; other baseline dates reused.
- At least one persistent PRISM-window cache hit.
- An unchanged repeat with no window builds or baseline/output modifications.

The timing report separately records whether the candidate meets the one-hour
target; scientific acceptance alone does not imply the latency target was met.
Source drift during the test is rejected. Setup, cloning and exhaustive paired
comparison are excluded from the reported incremental-cycle timings. No test
output replaces production data. Production remained serial during this test.

Incremental integration job: **4655814**, using 128 CPUs and 300 GB requested
scratch. Report: `forcing/work/nrt-incremental-staged/job_4655814/acceptance.json`.
Log: `forcing/logs/nrt-incremental-staged-4655814.out`. The integration passed
444 unit tests before submission.

### Acceptance and activation

Job 4655814 completed successfully. Staged incremental processing took 1,680
seconds (28 minutes), versus 2,362.6 seconds (39m23s) serial: 29% less time.
Exactly one baseline was rebuilt, seven were reused, one PRISM window was restored
from cache, and two were recalculated. Two final days were published and five
remained unchanged. Exact baseline/final comparisons passed. The unchanged
repeat took 16.2 seconds, with no window builds or baseline/output modifications.
The 28-minute timing excludes seed preparation, external refresh, queueing and
the separate exhaustive paired comparison.

Following user approval, `config/nrt_gfs.toml` now sets
`revision_pipeline = "staged_v1"` for future revision submissions, using 4/4/2
workers, 128 CPUs and 300 GB scratch. No crontab edit is required. Existing
submitted jobs retain their pinned pipeline; latest-hour extension and retro
production are unchanged. Rollback is `revision_pipeline = "serial"` for new
submissions. Activation itself submits no jobs and rewrites no forcing files.
