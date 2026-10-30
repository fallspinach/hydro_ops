# Canonical CNRFC: expanded-grid promotion

The user authorized replacing the original grid on October 29, 2026. Canonical
**`cnrfc`** becomes the expanded grid, with immutable version identifier
**`cnrfc_hu18_v1`**: 1,343 × 857 at 1 km, 681,063 active model cells and 691,832
forcing cells. Its reporting region is official CNRFC union WBD HU2 18, plus a
single 20-km support buffer. This does not alter the separate official-CNRFC
precipitation-treatment mask used in CONUS Stage-IV processing.

## Fail-closed pipeline

`nwm/status/cnrfc-domain-migration/state.json` blocks CNRFC forcing/model
submissions during migration. CONUS and unrelated domains continue. No live
crontab changes are needed. `config/cnrfc_nrt_daily.json` remains enabled, but
the runtime gate takes precedence until all migration stages pass.

```bash
source bin/project_environment.sh
"$HYDRO_OPS_PYTHON" bin/submit_cnrfc_grid_migration.py
```

The submitter refuses an existing journal, submits promotion held, records all
pipeline job IDs, then releases promotion to avoid queue-audit/submission races.
All jobs use ordinary shared resources, never NRTRES for `mpan`.

1. **Audit/promotion:** validate every new forcing receipt, accepted model
   segment, hourly record receipt and monthly endpoint restart pair. Require
   expanded forcing to include every old forcing filename and reject retirement
   of a newer original model endpoint. Lock both grids' writers.
2. Preserve an explicit per-file old-grid deletion manifest and compact original
   provenance archive. Rename original trees into the migration quarantine and
   expanded trees to `cnrfc`, on the same filesystem. Flatten output/restart
   campaign nesting, but retain campaign IDs in runs/status. No compatibility
   symlinks or NetCDF rewrites are introduced.
3. Rewrite moved JSON/JSONL paths, preserving exact originals in a compressed
   journal. Update parameter-manifest hashes for changed JSON only. Binary masks,
   parameters and model/forcing values retain their accepted identities/hashes.
   Repeat the publication audit after promotion.
4. **Synchronization test:** canonical NRT hourly/daily/monthly synchronization,
   explicitly using `--migration-test`. Unchanged files skip; real source
   revisions receive normal crop/transfer audits.
5. **Replay test:** October 1–3 warm start, daily checkpoints, October 2–3
   continuation, state comparison and private-mirror publication/merge tests.
   Only `--test --migration-test` can bypass the gate; canonical model products
   are not overwritten by this test.
6. **Old-grid retirement:** after both tests pass, re-audit replacements and
   verify the provenance/deletion-plan hashes and **every** exact target identity
   before unlinking files. Delete only enumerated quarantine files, remove empty
   directories, then restore submissions by setting `blocked=false`.

Any failure leaves operations gated. This is not an atomic multi-directory
snapshot: consumers must wait for state `complete`. Inspect the journal before
retrying a partial migration. Promotion/retirement run on SLURM with four CPUs;
source-wide audits/deletion are not login-node work.

## Canonical layout and retained evidence

```text
nwm/static/domains/cnrfc/
  manifest.json                   domain=cnrfc, grid_version=cnrfc_hu18_v1
  masks/cnrfc_masks.nc
  boundaries/cnrfc_boundaries.gpkg
  boundaries/sources/official_cnrfc.gpkg
  parameters/
forcing/outputs/cnrfc/{retro,nrt}/{hourly,daily,monthly}/...
nwm/outputs/cnrfc/{retro,nrt}/{hourly,daily}/YYYY/MM/...
nwm/restarts/cnrfc/{retro,nrt}/YYYY/MM/...
nwm/runs/cnrfc/{retro,nrt}/<campaign>/...
nwm/status/cnrfc/grid-promotion/handoff-acceptance.json
nwm/status/cnrfc-domain-migration/
  state.json, plan.json, submission.json, pipeline-jobs.json
  old-grid-deletion-plan.jsonl.gz, deleted-files.jsonl
  old-grid-provenance.tar.gz, metadata-before.jsonl.gz
```

Temporary old bulk data live only under
`nwm/work/cnrfc-domain-migration-v1/retired/` until the gates pass. The retained
archive contains original boundaries/masks, JSON/JSONL, namelists and model logs.
After deletion, old bulk NetCDF files cannot be recovered locally without
regeneration. New-grid data and restart pairs are never deletion targets. This
is user-authorized retirement of a superseded grid, not byte-equivalent baseline
cleanup; initial metadata identities/checksums and historical configuration
remain interpretable using the migration path map and preserved originals.

The new NRT model record starts April 18, immediately after the accepted retro
record. The old March–April NRT experiment is retired; the new retro covers those
dates, rather than pretending the two grids/streams are identical. New NRT
forcing starts March 2. Existing summary coverage is preserved, not extended by
promotion. Source selection, physical options, time/reduction conventions and
restart policy remain unchanged.

The daily configuration pins the new mask and promoted handoff gate. The first
normal replay may bootstrap from an older monthly checkpoint before establishing
the 11-day daily-checkpoint rewind. Retention remains plan-only. Legacy/candidate
builders and old retro-promotion workflows fail closed on the promoted grid.
Completed historical configs may retain versioned identifiers without requiring
directory aliases. USGS reporting lists remain valid because the geometry is
unchanged; future inventories use the canonical boundary and `cnrfc_hu18_union`.

## Submitted migration — October 29, 2026

| Stage | SLURM job | Required predecessor |
| --- | --- | --- |
| Full publication audit and promotion | **5064222** | Explicit runtime gate; released after pipeline journal |
| Canonical all-resolution NRT synchronization | **5064223** | Successful promotion |
| Isolated daily replay/continuation/publication test | **5064224** | Successful synchronization |
| Audited old-grid deletion and operational restoration | **5064225** | Successful replay test |

All four jobs completed successfully. Pre/post promotion audits matched, the
canonical forcing synchronization passed, and the October 1–3 daily replay
passed restart continuation and publication checks. The expanded grid is now
canonical `cnrfc`; the runtime block is cleared and operational submissions
are restored. The pre-submission software suite passed **577 tests**, with
three skipped.

Audited retirement removed 158,183 old-domain files (about 10.46 TB of logical
file volume, not measured physical space freed). The old bulk archive would
require regeneration to recover. Compact provenance, exact metadata originals,
the deletion plan and deletion journal are retained under
`nwm/status/cnrfc-domain-migration/`. Its `state.json` reports `complete` and
`blocked: false`; this is the authoritative migration completion record.
