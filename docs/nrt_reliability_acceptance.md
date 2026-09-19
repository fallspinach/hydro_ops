# Operational reliability acceptance

Started 2026-09-19 UTC. Cron is **not installed** on the inspected host
(`login1`). Submission is not acceptance: all reports must pass before enabling
scheduled production. No existing production writer was cancelled or redirected.

## Real-data tests

| Job | Test | Acceptance |
| --- | --- | --- |
| 4580877 | PRISM Tmin/Tmax +1 K private revision | T2D/Q2D/LWDOWN respond; rainfall and baselines unchanged; exact fresh-reference comparison; repeat no-op |
| 4580878 | Three-day NLDAS arrival, August 23–25 | Replace 72 HRRR/GFS hours; only three target baselines rebuilt; all three finals match fresh references; repeat no-op |
| 4580879 | Two incremental worker cycles, daily revisit, failure/retry | Daily revisit no-op; exclusive writer lock; injected worker failure preserves accepted files; retry converges |
| 4580880–4580884 | External source refresh | Inspect source reports, completeness and failures, not merely scheduler exit status |
| 4580885 | September 16–17 actual-source cycle after refresh | Isolated cold build followed by unchanged repeat; reports actual GFS use and elapsed times |
| 4580889 | CONUS optimized-forcing model read after 4580879 | 24-hour run crosses midnight using next-day forcing; 6-hour restart continuation; model completion and restart audit |

### Results checked 2026-09-19

All four forcing acceptance reports are **passed for correctness**, not an
unconditional operational latency acceptance:

| Job | Measured work | Unchanged repeat | Result |
| --- | --- | --- | --- |
| 4580877 | Temperature revision: 12m 39s | 33s | No baseline rebuilds; coupled fields changed; rainfall unchanged; fresh-reference match |
| 4580878 | Three-day NLDAS replacement: 1h 45m 19s | 14s | Exactly three baselines rebuilt; all 72 hours replaced; fresh-reference matches |
| 4580879 | First cycle: 65m 18s; next cycle: 32m 5s | Daily revisit: 22s | Lock exclusion, failed-worker preservation and retry passed |
| 4580885 | Two-day cold worker: 1h 20m 46s | 39s | 48 GFS hours; accepted publication and baseline reuse |

Job 4580885 took **2h 4m 36s** from refresh submission to first publication,
including 43m 51s before worker execution. Thus both its cold-worker and full
end-to-end latency exceeded one hour. Three-day replacement belongs in the daily
catch-up lane, not the lightweight six-hourly lane. Whole benchmark elapsed times
(including setup/reference work) were 2h 15m, 6h 15m, 1h 40m and 1h 21m respectively.

All five source-refresh jobs completed. Logs still reported unpublished Stage-IV
archive data and an unavailable latest MRMS product; scheduler success does not
mean every requested source hour exists. PRISM refresh took about 28.5 minutes
and checked a broader revision period, another reason to separate broad refresh
from the six-hourly critical path.

Model job **4580889 remains pending for scheduler priority**, with its dependency
satisfied. No model result is claimed. Cron remains uninstalled.

Real-data scenarios use 64 CPUs and 240 GB reserved scratch each; model test
uses 120 MPI ranks and 240 GB scratch. Three-day arrival includes expensive
seed and independent-reference builds: job elapsed time is not update latency.
The model test disables history output and is a forcing-read/restart smoke test,
not a hydrologic skill or output-publication acceptance test.

Reports:

- `forcing/work/nrt-reliability-20260919T063905/{temperature,nldas-three-days,worker-sequence}/acceptance.json`
- `forcing/work/nrt-operational-test-20260919T063912/acceptance.json`
- `nwm/outputs/tests/conus/nrt_reliability/job_4580889/`

The revision tests copy PRISM into private directories. Source refresh updates
the normal input archive; it does not publish production NRT forcing. If a
concurrent source refresh changes a test's recorded source identity, investigate
and repeat against stable inputs rather than relaxing the identity assertions.

## Small-fixture checks

51 checks passed before submission across NRT cycle, window cache, GFS
publication, coordinator scheduling, PRISM scheduling, revision perturbation,
NRT-to-retro handoff, and model restart planning tests. These include cache
corruption fallback, failure preservation and locking. They are not claims of
live SLURM or network-fault injection coverage.

## Remaining gates before cron

1. Review all real-data reports and resolve any failures. Require incremental
   six-hourly work comfortably under one hour; report daily catch-up separately.
2. Exercise the actual `update_nwm_forcing.py` submission/coordinator chain,
   including six-hourly versus daily overlap and source-job failures. The worker
   sequence above runs real `run_cycle`, but does **not** emulate cron timing or
   establish end-to-end scheduler correctness.
3. Confirm cron host policy, noninteractive conda/SLURM environment, logging and
   permissions. Check current writers and locks, including the pending NRT portion
   of the post-2020 repair array, before scheduling shared-output publication.
4. Preserve any existing crontab, install the reviewed repository template on
   exactly one scheduler host, then monitor actual six-hourly/daily production.

Use `bin/submit_nrt_reliability_tests.py --submit` for a new isolated campaign.
It submits the three worker/revision scenarios only; source refresh and the
dependent model test are explicit separate submissions. It never installs cron.
