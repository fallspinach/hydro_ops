# Relocating Hydro Ops

This is a preparation/runbook, **not authorization to move live data**. Project
relocation is distinct from adding `hourly/` beneath forcing stream directories.
Neither operation should occur under active writers or model readers.

## What is portable now

- `hydro_ops.config.load_settings()` resolves relative entries in
  `config/project.toml` and optional `config/local.toml` against the project root.
  It finds `config/project.toml` from the working directory or honors
  `HYDRO_OPS_PROJECT_ROOT`. Set the override for programs launched elsewhere.
- `bin/run_cron.sh` discovers its project from the checked-out script location;
  `HYDRO_OPS_PROJECT_ROOT` overrides discovery and must identify an absolute valid
  project directory. It sources `bin/project_environment.sh` and exports the root
  and `HYDRO_OPS_PYTHON` for child commands.
- `config/site.env` holds the current AWARE Python/SLURM defaults. Environment
  settings override defaults; optional ignored `config/site.local.env` is loaded
  last and may override them. These are **trusted executable shell files**: do
  not source files supplied by untrusted users or store credentials in them.
- `cron/hydro_ops.crontab.in` is the portable schedule source;
  `cron/hydro_ops.crontab` is its current-site rendering. Generate a proposed
  destination schedule without installing it:

  ```bash
  python bin/render_crontab.py --project-root /new/location/hydro_ops
  ```

  Paths containing spaces are quoted. Percent/newline characters are rejected
  because cron interprets them specially. Shell and cron paths remain absolute
  **at deployment time**; the generator avoids maintaining them by hand.

An example local site override (edit values for the actual destination):

```bash
HYDRO_OPS_ENV_BIN=/new/software/envs/hydro-ops/bin
HYDRO_OPS_SLURM_ROOT=/site/slurm
HYDRO_OPS_SLURM_CONF=/site/slurm/etc/slurm.conf
HYDRO_OPS_CLUSTER=destination_cluster
```

Moving only the project on the same cluster normally does not require changing
these software settings. Machine modules/partitions/accounts remain site-specific.

## Inventory remaining dependencies

```bash
python bin/audit_project_portability.py
python bin/audit_project_portability.py --old-root /previous/project/location
```

This read-only report lists filenames/line numbers, not potentially sensitive
configuration contents. It includes current untracked scripts, unlike `git grep`.
It excludes local secret files, generated archives, and runtime manifests. Expected
matches include site defaults and the rendered cron file. **It is not a migration
readiness certificate.** Existing site-specific SLURM/test/benchmark launchers are
not all converted by this preparation; review their matches before resubmitting.
Runtime JSON/JSONL, namelists, executable builds, symlinks and queued submissions
require a separate inventory. Do not change active campaign files speculatively.

New Python entry points should use the shared configuration/root rather than a
literal repository location. Shell entry points can source the common environment
helper. Batch submissions must capture/export the resolved project root and set
an explicit working directory. **Do not discover the project from a running
SLURM batch script's `BASH_SOURCE`**: that script is copied into a scheduler spool.
Existing queued jobs retain submission-time paths even after source files change.

## Maintenance-window procedure

1. **Freeze launches.** Preserve installed crontabs and disable the schedule on
   its one host. Pause automatic controllers and arrange NWM checkpoint boundaries.
   Inventory running/pending jobs, array descendants, interactive users and other
   readers. Wait for safe completion; deliberately hold/cancel/resubmit pending
   jobs as appropriate rather than assuming their paths update automatically.
2. **Record and back up.** Save code revision plus uncommitted changes, settings,
   environment specifications, task manifests, job IDs, and a file inventory
   (relative path, size, timestamp, available checksum/audit identity). Keep the
   migration journal outside any location that might be overwritten. Protect
   credentials and preserve their restrictive permissions.
3. **Transfer while quiescent.** A same-filesystem directory rename is cheapest.
   A cross-filesystem copy needs adequate space, metadata/permission preservation,
   and source/destination checksum verification. Retain the old copy until the
   new installation passes acceptance. Inventory symlinks, including targets
   outside the project; don't blindly dereference them or delete the old tree.
4. **Configure the destination.** Set site/local overrides and project root. Check
   external source locations, caches, credential paths and node-scratch settings.
   Recreate or correctly relocate Conda environments if their prefix changes—do
   not assume a copied environment is relocatable. Reinstall any editable package
   registration (`python -m pip install -e /new/location/hydro_ops`) with the intended
   interpreter. Check WRF-Hydro binaries/build paths, RPATHs and library modules.
5. **Rebind operational references.** Review exact old-root → new-root mappings in
   active manifests, task lists, namelists, status configuration and restart pairs.
   Prefer project-relative references for new schemas, with resolved paths only
   at execution. Preserve original absolute paths as historical provenance.
   Do not blanket-replace historical logs or scientific NetCDF metadata. The older
   `rewrite_manifest_paths.py` handles a different internal-layout migration and
   is **not a general relocation tool**; a reviewed relocation mapping is still
   required. Recreate pending submissions with new paths and dependencies.
6. **Reconcile identities safely.** Same-filesystem renames preserve inode/size/mtime
   but can still change path-based fingerprints. Cross-filesystem copies change
   inode/device IDs even if contents match. Only after checksum verification,
   record old and new identities in a relocation receipt and rebind operational
   audits/fingerprints. Do not bypass validators, fabricate new scientific audits,
   or unnecessarily regenerate forcing. The current summary path normalization
   covers only the approved `hourly/` insertion and summary filename change;
   arbitrary project-root changes are NOT normalized automatically.
7. **Validate before enabling.** Run `bash bin/run_cron.sh --check` with a minimal
   environment, then coordinator dry runs. Verify package imports resolve to the
   new checkout, source archives and credentials are readable, masks/weights are
   available, and accepted files/restarts match the verified inventory. Test
   year-boundary forcing lookup, daily/monthly summary reuse, status reporting,
   and a small NWM restart/forcing-read run. Dry-run checks do not prove live
   downloads or compute-node jobs work; test those deliberately before full rollout.
8. **Enable one host.** Render/review the new schedule, preserve any unrelated cron
   entries, then install on exactly one login node. Observe the first live cycle
   and descendants. Resume additional work only after the acceptance gates pass.

## Rollback

Before new production begins, stop all new readers/writers, reverse the recorded
rename or return to the retained verified source copy, and restore the saved
configuration/manifests and one-host schedule. After new writes begin, pause again
and reconcile new products/checkpoints first; never overwrite them with the old
snapshot. No automatic destructive cleanup or compatibility links are provided.

## Preparation validation

Tests exercise a copied wrapper under a different path (including spaces), explicit
site overrides, invalid-root rejection, argument/exit-code preservation, and cron
rendering. Live login1 checks use a minimal environment and do not install cron,
submit jobs, move archives, or rewrite runtime audit records.
