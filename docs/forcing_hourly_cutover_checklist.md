# Hourly-directory cutover — authorized, acceptance gates pending

Prepared September 23, 2026. No archive directories, production defaults,
operational receipts, or queued job definitions have been moved or rewritten.

## Current gates

- 1985 NWM **4524570** completed successfully (32h15m18s). Its yearly and
  December acceptance records passed, including the paired 1986-01-01 restart.
- 1986 **4524571** remains held by the user (Priority=0, JobHeldUser).
  Its dependency is now satisfied; releasing it would allow immediate scheduling.
  Later NWM years remain behind it; keep that hold until all read tests pass.
- September recovery arrays **4623979/4623980** passed; all 13 repaired NRT
  days passed publication validation. September 14–20 remain published.
- January/March 2019 recovery passed the 652-day block audit.
- Approved baseline cleanup is complete for 1995–2001 and 2003-01-02 through
  2020-10-12. Retained boundary days are still present.
- Read-only inspection found no installed cron entries on login1/login2 at
  preparation time and at the operational-gate launch. Recheck at cutover.
- Full-size isolated schema test **4626634** passed in 23m22s. Normal NRT
  repair plus unchanged-input reuse gate **4627429** is running. Do not move
  paths or change live defaults while this writer is active.

Snapshot: `forcing/status/layout-migration/preparation-20260923.json`.
It includes exact year-directory moves and contained file inode/size/mtime
identities, conflicts, all user jobs, and a reference-file review list. It is
not a maintenance lock and must be regenerated after 1985 ends.

## Path-edit worklist (apply only at cutover)

| Area | Required change |
|---|---|
| Stream helpers and root validation | Distinguish domain/stream container from hourly input/output root; validate `/stream/hourly` without losing stream isolation |
| Baseline/PRISM/NRT workers and convergence | Resolve hourly paths consistently, including directly assembled paths and frozen task JSON/env values |
| Source-aware NRT receipts/cache | Rebind published paths and baseline path identities; preserve scientific source identity; verify unchanged-input no-op does not rebuild data solely for relocation |
| Daily/monthly summaries | Read `stream/hourly`, continue writing to sibling `stream/daily` and `stream/monthly`; preserve compatible source signatures |
| Status and subsetting | Discover the resolution explicitly; keep domain-specific aligned-grid behavior |
| Cleanup and audits | Resolve moved replacement/audit references; preserve completed deletion manifests/journals as historical evidence, never replay them |
| NWM `production.run_segment` | Change the forcing root used for `check_forcing` and `INDIR`; currently hardcoded to `forcing/outputs/conus/retro` |
| Queued NWM jobs | Review their frozen batch script/environment and subsequent generated namelists; do not assume editing the submitter fixes queued jobs |
| Other scripts/config/docs | Review all files listed by the planner, including assembled paths that text searches cannot fully classify |

`layout_migration.map_dated_forcing_path` is an isolated, tested helper, not wired
into production. It maps only exact dated paths beneath a domain/stream into
`hourly`, is idempotent, and leaves daily/monthly/experimental/external paths
alone. Bare stream-root fields need semantic review: some are containers, others
are hourly roots. Do not globally replace every stream prefix.

## Controlled cutover sequence

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
Job completion alone does not execute migration: review acceptance, freeze
launches, finish the path-edit worklist, and regenerate the exact inventory.
The 1986 hold remains the safety barrier until post-cutover validation passes.
