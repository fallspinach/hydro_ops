# Gated 2003–2020-10-13 forcing campaign

`bin/submit_remaining_retro_forcing.py --directory <campaign> --submit` queues:

1. January and July 2003 production benchmarks concurrently, each with 21
   baseline workers and a 16-worker PRISM ceiling (12 CPUs per worker).
2. A gate requiring both complete months to pass final publication checks.
3. Nine sequential blocks: 2003–2004, 2005–2006, 2007–2008, 2009–2010,
   2011–2012, 2013–2014, 2015–2016, 2017–2018, and 2019–2020-10-13.

Each later block uses 42 baseline workers (504 CPUs) and up to 32 PRISM workers
(384 CPUs), plus lightweight controllers. Workers reserve 240 GB scratch and
check for at least 120 GB free before starting expensive processing. The
block wrapper has a 48-hour limit and waits for all descendant work rather than
returning success immediately after submission. Existing convergence provides
four bounded attempts. Failure or timeout blocks subsequent jobs; it does not
authorize partial success. Downstream blocks also require the durable gate file.

Benchmarks use the production 31-day PRISM batches: one month supplies only one
PRISM task, so these pilots measure seasonal per-worker cost and correctness,
not saturation of 32 PRISM workers. Block reports record elapsed time including
descendant waits, days per wall hour, resource limits, and descendant job IDs in
the referenced state. There is no NRT one-hour SLA applied to these retro runs.
Automatic release is based on correct, complete publication; slow-but-successful
benchmarks still release the requested campaign and expose timings for review.

New campaign PRISM submissions use the `validated_chunks_v1` writer profile:
compressed-chunk PRISM-window/calendar assembly and fast static masking, while
retaining active-gap domain repair and the current v4 static envelope. Corrected
PRISM fields receive full read-back verification; unchanged chunks receive
integrity verification. Fast masking trusts the immediately preceding embedded
domain audit, with full-read fallback for unknown audits and on month starts.
The original seasonal benchmark used reference writers and full masking audits.
Existing source science remains unchanged;
July 1–October 13, 2020 uses the existing CNRFC six-hour policy automatically.
The final audit checks every date's exact 00–23 sequence, accepted stable PRISM,
current mask checksum, identity-matched publication journal, and applicable
CNRFC provenance. It reuses production content audits, not a new independent
full-array scientific validation.

Benchmark files are production outputs and are reused by the first block.
Baseline cleanup is deferred throughout, retaining all neighbor support days.
No model simulation job, NRT schedule, or older repair campaign is changed.

The campaign directory contains `submission.json`, each block's state and
`.accepted.json` report, `gate.json`, and wrapper SLURM logs. Array logs remain
under the existing `forcing/logs/` paths. The final range contains 6,496 dates.

## September 14 benchmark repair

The first January/July attempts failed (schema mismatch, scratch exhaustion,
and a masking scope error), not scientific acceptance. Successful baselines are
reused. Original attempt states and baseline timings are preserved separately;
retry elapsed time must not be presented as a fresh baseline-to-final benchmark.

This campaign explicitly enables `HYDRO_OPS_RETRO_NEW_PRODUCTION=1`.
PRISM archive assembly normalizes only absent `precip_timing_source_id` fields
to zero (unavailable separate timing provenance), without changing existing
timing values or physical fields. Other schema differences still fail.
Calendar assembly, active-cell repair, and static masking happen on scratch.
The new mask scope permits only staged 2003-01-01 through 2020-10-13 files;
historical in-place and post-2020 repair authorizations remain unchanged.
A checksum-verified atomic transfer publishes each finished file and binds its
durable manifest/audit to the permanent file identity. Interrupted or PRISM-only
publications are not skipped by retries. The acceptance gate compares the
embedded mask grid checksum (`keep_sha256`), not the NetCDF container checksum.

Current retries are January **4525860** and July **4525861**, with PRISM arrays
**4525864** and **4525865**. Gate **4524971**
now requires both retries to succeed; the nine queued production blocks retain
their original chain. Initial states are saved as
`benchmark-200301.initial-failed.json` and `benchmark-200307.initial-failed.json`.
`retry-submission.json` records retry commands and IDs. A successful retry's
report explicitly labels its timing as reuse of existing baselines.
An initial retry (4525856/4525857) stopped at controller startup because SLURM
selected system Python. Controller and calendar-worker entrypoints now explicitly
re-exec `HYDRO_OPS_PYTHON` before importing the package. Those failed startup
states and submissions are preserved separately; they produced no new data.

## Validated writer adoption — September 15

User-approved after exact comparisons **4526251** (calendar/masking) and
**4526340** (PRISM-window writing), both passed. Calendar/masking reduced the
paired two-day runtime by about 22%; window writing saved a further 10–11% in
a separate paired trial. These are small-batch measurements, not a guarantee
of full-campaign throughput.

The profile is resolved in `bin/submit_prism_calendar_batches.py` when submitting
tasks marked `HYDRO_OPS_RETRO_NEW_PRODUCTION=1` for the retro stream. This reaches
the already-running 2003–2004 controller **4527692**, which was waiting on baseline
repair array **4529555** at adoption, as well as all later queued block wrappers.
No cancellation or resubmission is needed. Existing workers are not retuned;
baseline generation, NRT schedules, and older post-2020 repair jobs are unchanged.

The chosen profile is frozen into each task JSON and explicit SLURM exports,
printed in submission/worker logs, and recorded as `forcing_writer_profile` in
newly published files before their checksum/audit transaction. Later block state
files also record the profile. Existing complete outputs are reused, not rewritten
merely to change the writer. The `reference` submission profile remains available
for controlled rollback; unknown profiles fail. Both archive fast paths retain
reference fallback for unsupported encodings. Final scientific/publication checks
and scratch reservations remain unchanged.
