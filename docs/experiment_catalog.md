# Current workflows and historical experiments

Reviewed 2026-09-27. This is a navigation/safety index, not a new scientific
acceptance claim. Job-specific reports describe the code, inputs and policies at
that run; a `passed` receipt does not certify today's configuration.

## Start here for current operations

| Purpose | Current reference / entry point |
| --- | --- |
| NRT acquisition, extension and revisions | [NRT operations](nrt_gfs_operations.md), `bin/update_nwm_forcing.py` |
| Schedule and cron environment | `cron/hydro_ops.crontab.in`, [cron wrapper](cron_environment.md) |
| Forcing coverage/status | `bin/report_forcing_status.py` |
| Daily/monthly forcing summaries | [Temporal summaries](forcing_temporal_summaries.md) |
| CNRFC propagation | [CNRFC operations](cnrfc_forcing_operations.md), `bin/submit_cnrfc_sync.py` |
| CNRFC model/restart setup | [Domain](cnrfc_domain_boundary.md), [restart subsetting](nwm_restart_subsetting.md) |
| Model output correctness and recovery | [CHRTOUT recovery](chrtout_archive_recovery.md), [CONUS runs](conus_retro_simulation.md) |
| Time conventions | [UTC interval boundaries](nwm_time_conventions.md) |
| Regression tests | `python -m pytest tests` (no production submission) |

The latest staged-revision experiment is documented in
[revision parallelism](nrt_revision_parallel_benchmark.md). Its 4/4/2 configuration
is adopted, but its saved arrays remain experiment data, not published forcing.
The CNRFC model test scripts are retained; review their dates, restart donors and
isolated output locations before submitting them. No expensive test was submitted
as part of this cleanup.

## Retired launchers

Five obsolete launchers have been removed from the working tree, including the
temporary archived copies. Their source is retained in Git revision `7761087`
under `slurm/`; for example, inspect it with
`git show 7761087:slurm/test_nrt_baseline_schema.sh`. No historical launcher needs
to remain runnable just to preserve its implementation.

| Filename | Why retired |
| --- | --- |
| `test_nrt_baseline_schema.sh` | One-date September 15 mixed-schema experiment; canonical schema is now established. |
| `test_nrt_schema_operational.sh` | One-off repair that can write production September 15 data; not a generic safe test. |
| `test_hourly_layout_nwm.sh` | Completed directory-cutover gate, tied to an old campaign's 1985 receipt. |
| `test_nwm_nrt_midatlantic_20260310_48h.sh` | Uses March 9 23 UTC initialization and an old distance-based filling policy. |
| `test_nwm_monthly_prism_periods.sh` | Legacy Mid-Atlantic template and distance-based forcing filling; not current model acceptance. |

No active or pending job referenced these launchers in the scheduler inventory
at review. Current NRT controllers and CNRFC/CONUS production entry points were
left untouched. Unit tests remain active even when they cover historical bugs.

## Results: preserve evidence, distinguish it from products

- Published forcing lives under `forcing/outputs/{domain}/{stream}/{hourly,daily,monthly}`.
- `forcing/work/` contains intermediates **and** experiments; it is not a product archive.
- `nwm/outputs/tests/` contains test results, not production simulations.
- `nwm/recovery/` and all restart collections are protected recovery evidence.
- Historical reports remain at their original paths so provenance links still work.
  Selected superseded result directories carry `HISTORICAL_RESULTS.md`; the
  original machine-readable receipts remain, even when disposable arrays are removed.
  Receipts may therefore reference deleted historical arrays; they are evidence,
  not a current file-availability inventory.
- Unmarked work directories are **unclassified**, not implicitly approved/current.
  Do not delete work directories merely because their jobs have ended: benchmark
  chains and recovery tools can use old results as inputs.

The follow-up cleanup uses `bin/cleanup_historical_experiments.py`: a fixed-scope
plan/execute tool for the nine reviewed directories below, not a general cleaner.
It records exact paths and file identities in
`forcing/status/cleanup/historical-experiments-20260927.json`, retains and hashes
reports, and records deletion intents/completions in the adjacent `.journal.jsonl`.
Only a completed journal is evidence that deletion finished. Unknown file types,
symlinks, hard links and restart/input material are retained. Logical bytes removed
are reported; actual filesystem space reclaimed may differ due to snapshots.
Further data cleanup must inventory dependencies and retained receipts, inspect
live jobs, and produce another explicit deletion manifest.
In particular, do not remove the staged-revision reference/cache trees, old
CHRTOUT recovery material or protected restart hard links.

Completed 2026-09-27 at 18:43 UTC: **20 experimental array files removed,
62,567,988,572 logical bytes (58.27 GiB)**. All 109 retained files passed identity
checks and retained reports passed checksum verification after deletion. Six
affected result roots have `CLEANUP_COMPLETED.md` notices; three reviewed roots
had no eligible arrays. The removed arrays have no cleanup backup and would
require regeneration. The five removed launchers are recoverable from Git
revision `7761087`. Production forcing, model outputs outside these test roots,
restart files, recovery files and current benchmark reference/cache trees were
not deletion targets.

### Result directories labeled in this pass

Each path below now contains `HISTORICAL_RESULTS.md`. These local notices live
beside ignored runtime data; this tracked list records where they were placed.

```text
forcing/work/nrt-schema-v1-4626557
forcing/work/nrt-schema-v1-4626634
forcing/work/nrt-efficiency-4629922
forcing/work/post2020-stage-profile
forcing/work/precipitation-multiday-benchmark
forcing/work/chunk-calendar-benchmark
forcing/work/retro-prism-optimization-20260915T015927
nwm/outputs/tests/mid_atlantic/nrt_20260310_48h
nwm/outputs/tests/mid_atlantic/monthly_prism
```

## Documentation lifecycle

Documents marked **Historical experiment/recovery record** retain old commands,
job states and timing observations for traceability. Those statements are not
instructions for current operations. Their banners link back here and to the
current operational sources. Old benchmark timing is not a current service-level
estimate; workload, caches, source availability and resource settings differ.

New experiments should record the Git revision, inputs/masks, dates, resources,
isolated output root, acceptance criteria, and a current/superseded designation.
Promote implementation and documentation explicitly after acceptance, rather
than treating every saved pilot as another supported operational workflow.
