# Combined NRT writer operational acceptance

Job **4559177** completed successfully on 2026-09-18 (SLURM elapsed 2:15:54).
It produced isolated September 14–15, 2026 CONUS NRT outputs using actual source
availability, with no source-selection overrides. Both target days used HRRR with
GFS northern fallback for all 24 hours and received PRISM daily constraints.

Both adopted writer profiles executed successfully: four supporting baseline days
and two final calendar days used the compressed-chunk path, including intermediate
PRISM windows. Normal publication checks passed. The unchanged repeat preserved
both baseline and final file identities.

| Measurement | Result |
| --- | --- |
| Cold worker production | 8,107.2 seconds (2:15:07) |
| Launch to first-cycle publication, including queue/dependency wait | 8,149.6 seconds (2:15:50) |
| Unchanged repeat | 43.4 seconds |
| Target hours with GFS northern fallback | 48 |
| Functional acceptance | Passed |
| Cold production under one hour | No |

This validates combined writer compatibility and reuse, not the sub-hour latency
goal. The test built fresh private baselines from existing source archives; it does
not establish source-download latency, seven-day throughput, or incremental update
speed when some baselines already exist. A realistic changed-source incremental
test is the next performance measurement. Cron installation is not verified here.

Evidence: `forcing/work/nrt-combined-operational-test-20260918T135200/acceptance.json`.

The dependent automatic finalizer (4559178) was canceled before execution to allow
review of the subsequent mandatory-GFS and NRT-to-retro handoff changes. Those
guards were tested separately; this job is not evidence for their execution.

## One-day extension benchmark

Job **4559910** was submitted with 64 CPUs, 240 GB scratch, and a 12-hour limit.
`bin/benchmark_nrt_extension.py` replays extension from September 14 to 15 using
the accepted campaign above. It checksum-copies September 13–15 baselines and
the September 14 final file into an isolated campaign; only copied receipt
publication identities are rebased. Setup copying is outside the extension timer.

Expected work is one new September 16 support baseline, two PRISM windows in fresh
scratch, and one September 15 final file. September 14 and all retained baselines
must remain unchanged. The result is compared variable-by-variable against the
accepted cold-run September 15 output; comparison time is reported separately.
An immediate repeat must rewrite nothing. Source/configuration changes reject the
controlled comparison instead of being presented as one-day extension timing.

This uses the original source-selection as-of and existing source caches, not a
live refresh or a reconstruction of archive availability. It does not benchmark
NLDAS arrival, PRISM revision, or a ten-day inspection window.

Evidence, when available:
`forcing/work/nrt-extension-20260918T163803/acceptance.json`.
Submission is not acceptance; consult the report for completion and timings.

### Extension result and comparison correction

4559910 published the extension in 3,495.5 seconds (58:16), after 79.5 seconds
of isolated-copy setup. It rebuilt only the September 16 support baseline
(1,868.1 seconds); retained baselines and the September 14 final were unchanged.
The job then failed an overly strict benchmark gate: a regenerated baseline had
the same source fingerprint but a different whole-file checksum. This alone is
not evidence of changed scientific values. The unchanged repeat did not run.

The corrected gate checks source inputs independently of artifact checksums and
compares baseline and final variables, variable attributes, and selected policy
attributes. Validation-only job **4561008** checks existing outputs and attempts
a guarded unchanged repeat that refuses baseline rebuilds or reconciliation
subprocesses. Its separate `validation.json` preserves the original failure report.
Submission does not establish equality; inspect that report before accepting it.

Baseline log timings: nonprecipitation remapping 103.0 seconds, precipitation
remapping/compositing 600.6 seconds, hourly assembly 262.2 seconds, and daily
archive assembly 64.3 seconds. The remaining baseline time is not finely attributed
by this benchmark. Approximately 27 minutes of extension time lies outside the
new baseline build. It includes checks, PRISM processing, and final publication;
it must not all be attributed to PRISM remapping.

Next optimization candidates are dependency-validated PRISM-window reuse across
cycles (currently scratch-only), overlapping an already-ready PRISM window with
new support-baseline construction, and separate timing of native repair, GFS
publication, final audits, and transfers. These are proposals, not enabled changes.
Keep older revisions in the daily lane; do not silently remove PRISM constraints
from the six-hourly product solely to meet a performance target.

### Experimental cross-cycle window cache

Job **4561354**, dependent on successful validation job 4561008, benchmarks one
cross-cycle PRISM-window hit with 64 CPUs and 240 GB scratch. It copies retained
baselines privately, seeds the previous day's two windows, then runs reference
and cached extension reconciliation in separate fresh scratch directories.
The cached arm must reuse exactly one window and match all reference variables.
Seed/setup and comparison costs are separate from each measured arm. Reference
runs first, so filesystem warmth can influence results; interpret as an initial
benchmark, not a randomized throughput study. No baseline construction is timed.

The cache is opt-in through `HYDRO_OPS_NRT_WINDOW_CACHE`; production defaults are
unchanged. Dependency keys include baseline checksums, PRISM source identities,
revision, writer flags, forcing code identities, and fixed remapping assets.
Entries are immutable and atomically published under per-key locks. Restored data
are checksum-verified before use; changed/missing entries trigger recomputation.
Final publication audits remain unchanged. Cache retention and coordinated pruning
must be added before production adoption; no production cache is enabled here.

New per-publication receipt timings separate PRISM subprocesses, calendar assembly,
final audit, final transfer, and cache store/restore. Evidence will be under
`forcing/work/nrt-window-cache-paired-20260918/acceptance.json`.

Overlap scheduling and any narrower six-hourly revision policy remain separate
follow-up changes, gated on this comparison and stage timings. No current retro
job or operational schedule was altered by this experiment.

### Adoption and full-extension test

4561008 passed the existing-baseline/final field comparison and the unchanged
repeat (46.6 seconds). 4561354 passed with one persistent window hit: reference
1,078.2 seconds versus cached 736.9 seconds, a 341.3-second (31.7%) saving in
reconciliation/publication, excluding baseline construction. Compared values and
variable attributes matched. This was a reference-first, single paired trial.

The cache is now enabled in NRT configuration with 32-entry, 160-GB, 14-day
last-use retention and reader/publisher-safe pruning. This supersedes the opt-in
status above; rollback is `window_cache_enabled = false` (unless explicitly
overridden by the environment). Production publication checks remain intact.

Full-extension job **4572188** uses 64 CPUs and 240 GB scratch. It seeds the prior
cycle's cache outside the extension timer, then builds the missing September 16
support baseline and publishes September 15 using fresh scratch and one shared
window. All outputs/caches are private. It requires cold-reference field equality,
unchanged retained files, and an unchanged repeat. Setup and seed costs are
reported separately, not hidden in the measured extension. Results pending:
`forcing/work/nrt-extension-20260918T195530/acceptance.json`.

4572188 passed: full extension 2,518.7 seconds (41:59), including one new support
baseline at 1,756.1 seconds (29:16). Exactly one persistent window hit occurred.
Baseline/final comparison against the cold reference passed; unchanged repeat was
48.2 seconds. Setup (90.8 seconds), previous-cycle cache seeding (1,132.4 seconds),
and independent comparison (244.8 seconds) were outside the extension timer.
This uses existing source caches and does not measure source-refresh or queue time.

## Baseline worker scaling experiment

Job **4578181** compares complete September 16 baseline builds at the same
64-CPU/240-GB-scratch allocation: reference precipitation-remap/assembly workers
1/4 versus experimental 4/8. Native repair, GFS filling, and all audits remain
unchanged. Both outputs are private and must match each other and the accepted
baseline variable-by-variable, with matching source fingerprints.

The new `baseline_stage_timings` receipt field separates source planning/GFS
acquisition, complete-day production, native-donor repair, daily archive assembly,
GFS publication/audit, and transfer. Existing complete-day logs further separate
meteorological remapping, precipitation processing, and hourly assembly.
Benchmark-only environment overrides are `HYDRO_OPS_NRT_PRECIP_WORKERS` and
`HYDRO_OPS_NRT_ASSEMBLY_WORKERS` (1–16); production defaults remain 1 and 4.
This is a reference-first single pair, so filesystem warmth is a caveat.
Results pending: `forcing/work/nrt-baseline-workers-20260918/acceptance.json`.

4578181 passed. Full baseline time was 1,755.5 seconds at 1/4 workers versus
1,403.3 seconds at 4/8 workers (20.1% less). Precipitation processing fell from
612.4 to 390.0 seconds; assembly from 262.0 to 139.2 seconds. Both complete
baselines matched each other and the accepted reference. The 4/8 setup is now
adopted in `config/nrt_gfs.toml`; fingerprint normalization preserves previously
accepted baseline receipts despite the worker-count change.

## Native repair and GFS writer experiment

Job **4578828** benchmarks the remaining targets with 64 CPUs and 240 GB scratch.
Both arms use adopted 4/8 remapping/assembly workers. The experimental arm uses
four spawned native-repair processes, each operating on a different hourly file
with independent NetCDF handles, and GFS writes restricted to changed chunks.
Full native and GFS readback audits remain enabled. Chunk changes are detected
bitwise, including signed zeros; unsupported chunk layouts use the ordinary writer.
All fields and variable attributes must match both the reference arm and the
accepted baseline. The experiment does not enable these two changes in production.

Environment overrides: `HYDRO_OPS_NRT_REPAIR_WORKERS=4` and
`HYDRO_OPS_NRT_GFS_SPARSE_WRITES=1`; defaults remain 1 and disabled. Timing records
separate repair and GFS publication to attribute savings. This is a reference-first
single paired test, not a multi-day scalability study. Evidence pending:
`forcing/work/nrt-native-gfs-paired-20260918/acceptance.json`.

4578828 passed: full baseline 1,400.2 → 1,049.3 seconds (25.1% less).
Native repair was 338.2 → 97.7 seconds and GFS publication/audit 291.1 → 179.7
seconds. Compared variables and attributes matched both the reference arm and
the accepted baseline. Four repair workers and sparse GFS writes are now adopted
in NRT configuration, with source-fingerprint compatibility preserved.

Full combined extension job **4579030** was submitted with 64 CPUs, 240 GB scratch,
and all adopted defaults. It seeds the prior-cycle cache outside the extension
timer and verifies actual use of 4 remap / 8 assembly / 4 repair workers plus
sparse GFS writes. It must rebuild only one support baseline, reuse exactly one
PRISM window, match the cold-reference baseline/final fields, and complete an
unchanged repeat. All outputs are isolated; no external source refresh is timed.
Results pending: `forcing/work/nrt-extension-20260919T021605/acceptance.json`.

4579030 passed all combined acceptance checks: full extension **1,862.0 seconds
(31:02)**, new support baseline 1,088.8 seconds (18:09), unchanged repeat 45.7
seconds. All adopted worker counts and sparse writes executed; exactly one PRISM
window was reused. Baseline/final values matched the cold reference and retained
files were unchanged. Setup 144.0 seconds, cache seed 1,157.6 seconds, and independent
comparison 253.4 seconds were outside the extension timer. Total job elapsed was
57:45. This establishes the tested historical extension case, not source-download,
queue, NLDAS-arrival, or PRISM-revision latency. Those revision scenarios are the
next acceptance targets.
