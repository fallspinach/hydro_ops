# Forcing, NWM and observation project layout

For moving the entire project to another filesystem location or cluster, see
[project relocation](project_relocation.md). That is separate from the internal
temporal-resolution directory migration described below.

The repository operates two coupled production systems. `forcing` owns acquisition and production
of meteorological forcing; `nwm` owns WRF-Hydro/NWM domains, runs, state, and model products. A
forcing product is immutable input to an NWM run and is referenced rather than copied.
`observations/` owns provider observations and station metadata shared across
all domains; model-specific comparisons live under `nwm/validation/`.

```text
forcing/
  inputs/                         external NLDAS-2, HRRR, MRMS, Stage-IV, PRISM, and GFS archives
  static/                         source grids, elevations, and reusable remapping weights
  outputs/
    conus/{baseline,nrt,retro}/hourly/  calendar-day collections of hourly LDASIN
    conus/{nrt,retro}/{daily,monthly}/  summary products (not model input)
    validation/                   forcing validation products
  work/                           manifests, task lists, locks, and temporary coordination state
  logs/                           forcing acquisition and production logs
  status/                         machine-readable operational status

nwm/
  static/                         versioned domains, hydrofabric, and parameters
  inputs/                         model-specific dynamic inputs; shared observations are referenced
  runs/<domain>/                  experiment and cycle workspaces; large transient work uses scratch
  outputs/<domain>/<stream>/      published model history products
  restarts/<domain>/<stream>/YYYY/MM/
                                  paired, verified 00 UTC land and routing checkpoints
  logs/                           WRF-Hydro/NWM logs
  status/                         domain inventories and model-operation status

observations/
  static/stations/<provider>/     station and sensor metadata, source notes
  static/domains/                 station-to-domain/reach mappings when validated
  inputs/<provider>/              preserved source responses and revisions
  processed/<provider>/           standardized records, units and quality flags
  status/                         acquisition plans, coverage and update reports
  logs/                           acquisition/submission logs
  work/                           temporary observation processing state
```

For CDEC full-natural-flow records and daily cron acquisition, see
[CDEC observations](cdec_observations.md). Store station records once by provider,
resolution, station and year rather than duplicating them per model domain.
The [USGS streamflow inventory](usgs_streamflow_observations.md) similarly keeps
one CONUS station/series snapshot and derives reporting-region lists for the
four subdomains without duplicating observation records.

### CNRFC model publication

The [expanded-grid promotion](cnrfc_grid_promotion.md) makes the validated HU2-18
grid canonical `cnrfc`, with flat resolution/year/month publication directories.
Run/status campaigns retain their names; published forcing/output/restart paths
do not require a `cnrfc_hu18_v1` alias or temporary campaign nesting.

Validated CNRFC retrospective model products use these canonical destinations:

```text
nwm/outputs/cnrfc/retro/hourly/YYYY/MM/YYYYMMDD.CHRTOUT_DOMAIN1
nwm/outputs/cnrfc/retro/daily/YYYY/MM/YYYYMMDD.CHRTOUT_DOMAIN1.daily
nwm/outputs/cnrfc/retro/daily/YYYY/MM/YYYYMMDD.LDASOUT_DOMAIN1.daily
nwm/restarts/cnrfc/retro/YYYY/MM/
```

`recovery_record_stack_v1` is a recovery campaign identifier, not a permanent
publication level. Run records and logs retain that identifier for provenance.
The promotion audit/journal is `nwm/status/cnrfc/retro-promotion/`; its
`promotion.json` must say `complete` before the recovery runner uses these
canonical destinations. Earlier campaign/test directories are historical, not
the current published record. CONUS remains in its active recovery campaign
layout until it is separately audited and promoted.

The forcing domain name describes the output grid, not the source-product domains. The current
full NWM grid is `conus`; future subset products use stable lowercase identifiers such as `croton`
or `mid_atlantic`. `nrt` and `retro` remain separate in both systems. NWM run manifests record the
forcing domain, stream, date range, static-domain version, executable version, namelist checksums,
and exact input/output restart pairs.

Historical JSON and JSONL manifests were rewritten to canonical paths after migration. The former
compatibility symlinks below `data/`, `outputs/`, `work`, and `logs` have been removed; these names
must not be used as production inputs or destinations.

Configuration, command-line defaults, Python entry points, SLURM wrappers, cron, and generated
manifests use canonical paths directly. `bin/migrate_project_layout.py` still names the former roots
as one-time migration sources, but compatibility-link creation is disabled unless explicitly
requested with `--create-compatibility-links`. `bin/rewrite_manifest_paths.py` provides an
idempotent, atomic dry-run/execute migration for structured historical manifests.

## Temporal-resolution level — activated September 23, 2026

Producers and NWM readers now use `<domain>/<stream>/hourly/YYYY/MM/`.
The previous year directories were renamed without rewriting NetCDF data.
The layout is:

```text
forcing/outputs/<domain>/
  baseline/hourly/YYYY/MM/YYYYMMDD.LDASIN_DOMAIN1
  nrt/
    hourly/YYYY/MM/YYYYMMDD.LDASIN_DOMAIN1
    daily/YYYY/MM/YYYYMMDD.LDASIN_DOMAIN1.daily
    monthly/YYYY/YYYYMM.LDASIN_DOMAIN1.monthly
  retro/
    hourly/YYYY/MM/YYYYMMDD.LDASIN_DOMAIN1
    daily/YYYY/MM/YYYYMMDD.LDASIN_DOMAIN1.daily
    monthly/YYYY/YYYYMM.LDASIN_DOMAIN1.monthly
```

Resolution names describe the data, not file duration. Hourly records remain
packed into calendar-day files (00–23 timestamps); daily summaries use completed
01–00 endpoint samples with 00–00 bounds. Monthly summaries cover whole calendar
months. Temperature monthly min/max fields are means of daily extrema. See
[summary semantics](forcing_temporal_summaries.md). The same domain → stream →
resolution principle applies to NWM history products, but this migration does
not relocate NWM outputs, restart files, or external source archives.

Only independently produced domain baselines need storage. Routine baseline
daily/monthly summary products are not planned. NRT and retro remain separate.

### Preparation and cutover gates

Completed acceptance results and the reader/receipt worklist are recorded in
[the hourly cutover checklist](forcing_hourly_cutover_checklist.md). Gate 4627957
passed and released NWM 1986 after the NRT no-op and CONUS model tests passed.
The migration renamed 97 directories containing 35,752 files and rebound 17,777
JSON sidecars; data-file identities and existing summary freshness were preserved.
The pre-cutover operational repair/no-op test 4627624 passed. The exact inventory,
durable rename/metadata journal and original sidecar backups are under
`forcing/status/layout-migration/cutover-20260923/`. Code checkpoint: `d620802`.
Historical completed task files, logs, cleanup journals and content-audit evidence
remain historical; current dated paths in published JSON sidecars are rebound.
No compatibility symlinks were created. NRT's private `.prism-window-cache`
remains at the stream-container level and is not a published hourly product.

Run this read-only inventory while jobs continue:

```bash
conda activate hydro-ops
python bin/plan_forcing_resolution_layout.py
```

Use `--inventory-files --output PATH.json` to save a per-file identity snapshot
alongside the directory plan. This remains read-only for all forcing files and
has no execute mode. Save a fresh snapshot at the actual maintenance window.

It emits JSON with proposed year-directory renames, destination conflicts, all
current user SLURM jobs, and source/configuration/documentation files requiring
path review. It deliberately has **no execute mode** and never declares the
system safe solely because the queue is empty. It does not recursively scan the
archive unless `--inventory-files` is selected, or validate NetCDF contents.
Run it again immediately before cutover; this is a live plan, not a frozen job list.

The following sequence was used for this cutover and remains the checklist for
future coordinated migrations:

1. Let forcing chains finish and verify final coverage/audit reports.
   Completion estimates are not safety gates; retries and descendants must finish.
2. Arrange a pause at an NWM restart/checkpoint boundary. Review running and
   pending NWM jobs, including their frozen submission environments and namelists.
   Stop automatic chain advancement/launches during the maintenance window;
   do not cancel or hold jobs merely by running the planner. Check interactive
   readers, cron, status scans, and other users as well.
3. Prepare and review path edits for producers, PRISM controllers, baseline
   cleanup, repair scripts, source replacement, status discovery, subset tools,
   summary scripts, and NWM `INDIR`/forcing roots. Audit dynamically assembled
   paths too: textual matches are a review aid, not a complete dependency graph.
   Pending jobs with captured old paths must be updated or resubmitted deliberately.
4. Save the pre-migration inventory and rollback journal. Move only four-digit
   year directories beneath `hourly/`, retaining filenames and sidecars. Use
   same-filesystem renames; no NetCDF rewriting or recompression is needed.
   Never merge conflicting destinations. Leave `daily/`, `monthly/`, experiments,
   and unrelated files alone. No backward-compatibility symlinks are planned.
5. Rewrite operational manifest/task-list paths using exact, idempotent mappings
   that distinguish stream roots from already-qualified resolution roots. Preserve
   historical provenance and source/content checksums; do not blindly replace all
   occurrences of a stream prefix (which would nest daily/monthly under hourly).
   The older `rewrite_manifest_paths.py` does NOT yet implement this new mapping.
6. Validate counts, sizes, sidecars, and representative NetCDF reads against the
   saved inventory. Test year-boundary hourly lookup, daily/monthly aggregation,
   status reporting, subset extraction, NRT no-op planning, and an NWM forcing-read
   smoke test before releasing jobs. A migration must not trigger scientific
   reprocessing solely because a path changed.
7. Release producers/readers only after all gates pass. Before release, rollback
   can reverse recorded renames and restore saved configuration/manifests. After
   new production writes begin, rollback requires another coordinated pause and
   reconciliation—never overwrite newly published data.

`bin/migrate_forcing_hourly_layout.py` is the separate, explicitly gated executor.
It journals exact renames, backs up changed sidecars, and verifies all data-file
identities. `bin/verify_forcing_hourly_layout.py` checks representative archives,
NWM year-boundary lookup, unchanged daily/monthly summaries, status discovery,
and the CNRFC grid crop. Its checks do not release held jobs.
