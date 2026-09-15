# Static-mask compressed-chunk benchmark

The historical masking campaign remains unchanged while this experiment runs.
`bin/benchmark_static_mask_chunks.py` writes only a separate scratch candidate;
it cannot publish or replace an archive file. Benchmark job 4520734 uses
`forcing/outputs/conus/retro/2000/01/20000115.LDASIN_DOMAIN1`.

The existing writer decodes, hashes, and recompresses every variable. Its
representative measured runtime was 264 seconds for writing and 349 seconds
including validation and transfer. These numbers are historical observations,
not a controlled same-file comparison.

The experiment preserves the NetCDF schema and compression settings, copies
retained chunks without decompressing/recompressing them, reuses encoded fill
chunks outside the envelope, and rewrites only mixed boundary chunks. Auxiliary
chunked variables are also copied in compressed form. It currently requires
fixed dimensions and fully allocated forcing chunks with one hourly record per
chunk; unsupported layouts fail rather than silently falling back.

An independent NetCDF read-back compares every output variable against the
original, with the expected mask applied to the eight forcing fields. It checks
all active values, retained missing values, variable metadata, and compression
settings. Unit tests exercise copied, excluded, and boundary chunks, source
immutability, and rejection of missing active data.

The benchmark prints write time, full verification time, output size, and chunk
counts to `forcing/logs/static-mask-chunk-benchmark-4520734.out`. Its timings do
not include transfer to permanent storage or production manifest publication.
Do not equate a writer-only improvement with end-to-end campaign throughput.

Before production adoption: verify the CONUS benchmark, compare multiple source
files (including the partial 1979 first day), measure end-to-end publication and
bounded concurrency, and preserve existing lock, checksum, atomic replacement,
and resumable manifest safeguards. Do not change running campaign workers in
place. The prototype requires h5py, available in the current hydro-ops environment.

## Integrity-only audit experiment

Job 4520734 passed the full CONUS comparison: writing took 91.17 seconds and
verification took 133.35 seconds (224.51 seconds combined, excluding publication).

Job 4520860 compares the chunk-integrity verifier with the full verifier on the
same separate output. It compares compressed bytes for every unchanged chunk,
decodes changed boundary chunks, and checks each distinct excluded-chunk encoding
against an all-fill block. It also checks dimensions, attributes, data types,
chunking and filters. Corruption-injection tests cover each chunk class.

This establishes transformation integrity, **not source completeness or scientific
validity**. A future production integration must require a trustworthy source
audit tied to the actual file identity, or perform full validation instead. No
production activation or audit-trust decision is implemented by this benchmark.
The lighter audit runs first, then the full audit; cache effects and changing
storage load should be considered when interpreting the timings.

Job 4520860 completed successfully. On the same file, writing took 94.73 seconds,
chunk verification 53.90 seconds, and the independent full verification 142.52
seconds. Replacing full verification with chunk verification saves 88.62 seconds
(62.2% of audit time). Write plus verification becomes 148.63 instead of 237.25
seconds (37.4% lower). Both checks passed. The benchmark ran both audits for
comparison; normal integrity-only processing would run just the lighter one.
It compared 57,096 unchanged compressed chunks, decoded 16,320 boundary chunks,
and verified 11,904 excluded chunks using eight distinct fill encodings.
Publication/transfer costs are still excluded; no live campaign was modified.

## Parallelism and CDO reference

Job 4521007 runs `bin/benchmark_static_mask_parallel.py` on January 15–18, 2000.
It reserves 12 CPUs and 120 GB scratch for the entire comparison. Sequential
trials use 1, 2, and 4 independent chunk-writer processes; a fourth trial uses
one CDO process. Library thread counts are pinned to one to avoid unintended
oversubscription. The same four source files are used in each trial, without
modifying them. This is one trial per configuration, not a cold-cache or repeated
statistical benchmark; storage load and cache effects can influence results.

Each timed trial includes copying inputs to scratch, masking, integrity checks,
and copying outputs to a separate permanent benchmark directory with fsync and
SHA-256 verification. Per-stage times and aggregate files/hour are recorded in
`forcing/work/static-mask-parallel-benchmark/job_4521007/summary.json`; stdout is
`forcing/logs/static-mask-parallel-benchmark-4521007.out`. Candidates remain under
that benchmark directory, never the production archive. Scratch intermediates
are removed after each completed file. Four rounds retain sixteen benchmark
files (roughly 70 GB, depending on compression).

CDO uses `ifthen -selname,keep MASK -selname,T2D,... SOURCE` with NetCDF4 level-2
compression. Its reference includes only the eight forcing fields, not full
auxiliary-schema restoration; its timing is therefore favorable to CDO. Every
field and timestamp is checked. Chunk outputs preserve the auxiliary variables
and receive chunk-integrity verification. After the timed rounds, all twelve
chunk outputs additionally receive independent full audits, excluded from the
trial timings. The experiment does not enable lighter validation in production
or change the existing campaign. Fifteen focused tests pass, including a small
actual CDO invocation, transfer checks, and chunk-corruption detection.

## Campaign-concurrency scaling pilot

Build array **4521603** runs eight concurrent 12-CPU tasks, each with two writer
processes and eight distinct dates (64 days total, January–August 2000). Initial
placement was four tasks each on awr-2-08 and awr-2-48: 96 allocated CPUs total.
The shared partition limits each job to one node, so this uses array tasks like
the existing campaign rather than a multi-node allocation. Each task reserves
120 GB scratch. Existing production tasks remain untouched.

Independent full-audit array **4521611** depends on completion of the entire build
array, preventing full audits from overlapping and distorting the build timings.
Summary job **4521612** depends on all audits succeeding. Outputs and machine-readable
reports are under `forcing/work/static-mask-scaling/job_4521603/`; approximately
285 GB of separate candidates are retained. Build logs use
`forcing/logs/static-mask-scaling-4521603_*.out`.

The summary records elapsed time from first build start to last build finish,
aggregate files/hour, individual stage timings, host placement, and the common
overlap interval of all eight tasks. All 64 output files must pass independent
full audits. Compare the measured rate with the idealized 421 days/hour (eight
times the single-task two-writer measurement), allowing for source-season,
cache, and shared-storage load differences. Success is not automatic permission
to switch production: source-audit eligibility and publication integration are
still required. No production file is replaced by these jobs.

## Production integration (2026-09-13)

The scaling pilot passed all 64 full audits and measured 406.5 files/hour at
96 CPUs (97% of idealized scaling). Historical static-mask production now has
an opt-in `--fast` path; new submission configurations select it and two isolated
writer subprocesses per task. Old configurations retain the original writer.
Post-2020 rebuild jobs are unchanged.

The production wrapper retains per-day locks, before/after source identity checks,
checksum-verified transfer, atomic replacement and durable manifest recovery.
Exact embedded input audit
`all_records_all_8_fields_nldas2_rectangle_and_model_land_v1` permits chunk-integrity
verification; unknown audit labels require full read-back. Every month's first
day also receives full read-back. Source audit provenance and the chosen audit
mode are recorded in the publication journal. Embedded audits are trusted records
from this project's controlled archive, not validation of arbitrary external files.

Transition directory:
`forcing/work/static-envelope-v4-fast-20260913T125408/`.
Only pending tasks of 4519428 were cancelled; eight running tasks were retained.
Canary **4521634** waits for the old array to finish, then processes the first
remaining 14-day batch. **4521635** processes the other 522 batches at concurrency
eight only after the canary succeeds. These jobs share the original audit directory,
so already verified files are skipped and publication recovery remains resumable.
The cancelled queue entries are replaced, not deleted data. Sixteen focused tests
pass, including fast publication, unknown-audit fallback, and manifest recovery.
