# Incremental NRT source-revision tests

The validated optimization checkpoint was pushed as `27dc544`. The next tests
use `bin/benchmark_nrt_revisions.py`, private baseline/final/PRISM copies, existing
source caches, and separate scratch directories for setup, update, and reference.
They are controlled source-change experiments, not historical archive-availability
reconstructions or tests of external download latency. Each has 64 CPUs and
240 GB scratch. Production activation receipts and source archives are untouched.

## NLDAS-2 arrival: 4580030

August 25, 2026 initially selects HRRR for all target hours, with GFS northern
fallback. The test then restores normal selection of available NLDAS-2. Only the
target baseline should rebuild; supporting baselines must stay unchanged. Final
hourly primary-source provenance must be all NLDAS-2, and CF-decoded GFS cycle
timestamps must switch from 24 populated hours to zero. A separate reference
rebuilds the target NLDAS baseline and final day with window caching disabled.
The incremental baseline/final must match that reference. An unchanged repeat
must preserve all output identities.

**Passed.** The measured update took **44 minutes 57 seconds**; only August 25's
baseline rebuilt, all 24 hours switched to NLDAS-2 with zero GFS timestamps, and
baseline/final fields matched the independent reference. The unchanged repeat
took 5.8 seconds. Older supporting archives with different chunk layouts were
handled correctly, though not as efficiently as uniformly optimized layouts.
Evidence: `forcing/work/nrt-nldas-arrival-20260919/acceptance.json`.

## PRISM precipitation revision: 4580031

September 14–15 are first published using copied PRISM inputs. The copied
September 16 PRISM precipitation field is then increased by 1% at wet cells,
preserving dry/missing cells. That window affects September 15 but not September
14. Source identity changes naturally invalidate dependent receipts/cache keys;
no receipt is manually invalidated. The test requires zero baseline rebuilds,
unchanged September 14 output, changed September 15 rainfall, equality with a
fresh cache-disabled reconciliation, and an unchanged repeat. This does not yet
exercise PRISM temperature revisions.

**Passed.** The measured update took **12 minutes 59 seconds**, rebuilt no
baselines, preserved September 14, and matched the fresh reference. The unchanged
repeat took 57.5 seconds.
Evidence: `forcing/work/nrt-prism-revision-20260919/acceptance.json`.

Setup, measured incremental update, independent reference/comparison, and repeat
are reported separately. Submission alone is not acceptance. Both jobs may run
concurrently, so shared-filesystem contention is a timing caveat.

The subsequent temperature-revision and three-day NLDAS-arrival tests also
passed; see [operational reliability acceptance](nrt_reliability_acceptance.md)
for measured timings and the remaining cron gates.
