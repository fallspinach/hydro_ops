# Hourly-directory cutover — completed and accepted

September 23, 2026: all 97 inventoried year directories have been renamed into
`hourly/`. Every one of 35,752 file identities matched immediately after rename.
Production defaults and NWM INDIR use the new paths. Rebinding completed for
17,777 JSON sidecars, with original copies and a durable journal. All 17,767
non-JSON file identities were preserved; NetCDF files were not rewritten.
The inventory covered 78,153,611,930,679 bytes (about 71.1 TiB).

## Acceptance results

- 1985 NWM **4524570** completed successfully (32h15m18s). Its yearly and
  December acceptance records passed, including the paired 1986-01-01 restart.
- Completion gate released 1986 **4524571** after all checks passed. The 1986
  production run resumed; later years retain their existing dependency chain.
- September recovery arrays **4623979/4623980** passed; all 13 repaired NRT
  days passed publication validation. September 14–20 remain published.
- January/March 2019 recovery passed the 652-day block audit.
- Approved baseline cleanup is complete for 1995–2001 and 2003-01-02 through
  2020-10-12. Retained boundary days are still present.
- Read-only inspection found no installed cron entries on login1/login2 at
  preparation time and at cutover. This migration did not install any cron entries.
- Full-size isolated schema test **4626634** passed in 23m22s. Normal NRT
  repair/reuse gate **4627429** failed on stale-hourly input selection. The
  corrected exact-daily-input gate **4627624** passed (18m07s, no-op 8 seconds).
- Post-migration NRT gate **4627946** passed in 18 seconds: both cycles unchanged,
  with all baseline/final identities preserved.
- CONUS one-day model test **4627947** passed in 11m34s on 120 MPI ranks:
  24 hourly channel records, one daily land record, and a verified restart pair
  for 1986-01-02. Model products/restarts were scratch-only; logs and
  acceptance are retained in `nwm/runs/tests/hourly-layout-4627947/`.
- Completion gate **4627957** passed in 29m22s, including its wait for metadata
  completion. It verified both test reports, repeated read checks and released
  only NWM job **4524571**. The release receipt is `release-gate.json` below.
- The full regression suite passed: **374 tests** (one existing deprecation warning).

Execution snapshot: `forcing/status/layout-migration/cutover-inventory-20260923.json`.
It includes exact year-directory moves and contained file inode/size/mtime
identities, conflicts, all user jobs, and a reference-file review list. It is
not a maintenance lock. The execution journal, original metadata backups and
verification reports are in `forcing/status/layout-migration/cutover-20260923/`.
The read verification passed for seven representative files, NWM year-boundary
lookup, unchanged daily/monthly summary reuse and a CNRFC aligned crop.
Rollback code checkpoint: **d620802**. Production has resumed: before any rollback,
freeze launches again and reconcile files created since cutover. Then
restore sidecars from `metadata-before`, reverse completed journal renames, and
restore code from that checkpoint. Do not replay historical cleanup plans.

## Completed path-edit scope

| Area | Required change |
|---|---|
| Stream helpers and root validation | Distinguish domain/stream container from hourly input/output root; validate `/stream/hourly` without losing stream isolation |
| Baseline/PRISM/NRT workers and convergence | Resolve hourly paths consistently, including directly assembled paths and frozen task JSON/env values |
| Source-aware NRT receipts/cache | Rebind published paths and baseline path identities; preserve scientific source identity; verify unchanged-input no-op does not rebuild data solely for relocation |
| Daily/monthly summaries | Read `stream/hourly`, continue writing to sibling `stream/daily` and `stream/monthly`; preserve compatible source signatures |
| Status and subsetting | Discover the resolution explicitly; keep domain-specific aligned-grid behavior |
| Cleanup and audits | Resolve moved replacement/audit references; preserve completed deletion manifests/journals as historical evidence, never replay them |
| NWM `production.run_segment` | `check_forcing` and `INDIR` now use `forcing/outputs/conus/retro/hourly` |
| Queued NWM jobs | Review their frozen batch script/environment and subsequent generated namelists; do not assume editing the submitter fixes queued jobs |
| Other scripts/config/docs | Review all files listed by the planner, including assembled paths that text searches cannot fully classify |

`layout_migration.map_dated_forcing_path` is used by the migration utility.
It maps only exact dated paths beneath a domain/stream into
`hourly`, is idempotent, and leaves daily/monthly/experimental/external paths
alone. Bare stream-root fields need semantic review: some are containers, others
are hourly roots. Do not globally replace every stream prefix.

## Controlled cutover sequence (historical record, not a rerun instruction)

1. Confirm 1985 COMPLETED successfully and its final paired restart accepted.
   Confirm 1986 remains JobHeldUser; inspect every producer/reader and both crons.
2. Regenerate inventory; block on conflicts, changing identities, or active users.
   Save current code/config/task-list/operational-receipt versions separately.
3. Implement and test the worklist above while launches remain frozen. Keep
   unrelated source archives, model outputs/restarts, and daily/monthly products
   unchanged. No compatibility symlinks are planned.
4. Rename only inventoried four-digit year directories on the same filesystem,
   with a durable intent/completion journal and inverse rename for every move.
   No merging, recompression, or scientific data rewriting.
5. Apply reviewed operational path mappings. Keep historical logs and completed
   cleanup evidence historical; do not rewrite provenance indiscriminately.
6. Compare file counts, byte totals, inode/mtime identities, and sidecars to the
   snapshot. Open representative files from every stream/domain and old-era
   monthly-PRISM, Stage-IV, CNRFC-corrected, and HRRR/GFS periods.
7. Test December 31/January 1 hourly lookup, daily/monthly summary reuse, status,
   domain extraction, source-aware NRT no-op/fingerprints, and an NWM forcing-read
   smoke test using the new INDIR. No path-only change should trigger reprocessing.
8. Only after acceptance, resume operational launches and release 1986 deliberately.

Before releasing writers, rollback restores saved code/operational metadata and
reverses the journaled directory renames. After new publication begins, rollback
requires another freeze and reconciliation; never overwrite newly created data.

The user authorized this gated sequence after the successful schema test.
The one-time execution and completion scripts retain the original gate/job IDs
for reproducibility; do not resubmit them against the completed migration.
Any future migration needs a new reviewed inventory, acceptance gates and pause.
