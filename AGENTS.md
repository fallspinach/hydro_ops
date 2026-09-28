# Hydro Ops agent guide

This repository operates shared, long-running forcing and WRF-Hydro workflows.
Use this file for durable project rules; consult the linked documents only when
relevant to the task. Job IDs, coverage endpoints and benchmark results belong
in status reports and campaign documentation, not here.

## Environment and checks

Run commands from the project root. For Python tools and SLURM clients:

```bash
source bin/project_environment.sh
"$HYDRO_OPS_PYTHON" -m pytest tests/test_relevant_module.py -q
"$HYDRO_OPS_PYTHON" -m pytest -q
git diff --check
```

Replace the illustrative test filename with the affected tests. Run focused
tests while developing and the full suite for cross-cutting changes. Use Ruff
from the configured environment on changed Python files. Documentation-only
changes generally need link/path and diff checks, not production jobs.

- `config/site.env` and ignored `config/site.local.env` hold site settings;
  use `HYDRO_OPS_PROJECT_ROOT`/existing path helpers rather than adding absolute
  project paths. See [relocation](docs/project_relocation.md).
- The environment script intentionally replaces PATH/library settings. It is
  a login-node launcher, not the MPI runtime setup. Model SLURM entry points
  load their own compiler/MPI/NetCDF modules; preserve that separation.
- Use `bin/run_cron.sh` for cron execution; do not depend on interactive shell
  initialization or Conda activation. Never print or commit credentials.
- Do not run CONUS processing, large downloads or model simulations on login
  nodes. Use appropriately scoped SLURM jobs for heavy validation.

## Operational safety

- Inspect current jobs, dependencies and campaign manifests before submitting
  replacements or changing shared scripts used by pending/running jobs.
  Preserve locks, acceptance gates, atomic publication and duplicate-writer
  protection. A status request does not authorize cancellation or resubmission.
- **`mpan`: never request NRTRES**, including as a queue-delay workaround.
  `cw3ehydro` is the operational account: explicitly configure NRTRES for
  time-critical NRT jobs, but prefer ordinary resources for monthly retro work.
  Reservation ACL membership does not override this project policy.
- Verify actual resource allocation: this cluster may not honor `--mem` as
  expected. Follow validated CPU/memory/scratch requests in the relevant runner.
  Use `/scratch/{SLURM_JOB_USER}/job_{SLURM_JOB_ID}` for temporary large files;
  request sufficient scratch and preserve accepted outputs/restarts before exit.
- Keep cron on **one host only**. Saved schedules are in
  `cron/hydro_ops.crontab` and its `.in` relocation template. Update both for
  schedule changes; installation is separate and requires user authorization.
  See [cron/account policy](docs/cron_environment.md).
- Do not delete baseline files merely because a retro filename exists. Require
  accepted replacements and an explicit audited deletion plan. Protect restart
  files, recovery originals and boundary days needed by adjacent work. See
  [baseline cleanup](docs/baseline_cleanup_manifest.md) and
  [experiment catalog](docs/experiment_catalog.md).
- Report submission, execution, publication and scientific validation separately.
  `COMPLETED` alone is not a publication audit. Use job-specific reports rather
  than assuming a mutable `latest.json` belongs to the job being investigated.

## Layout and scientific invariants

- The top-level subprojects are `forcing/` and `nwm/`; do not reintroduce old
  `data/forcing/` or `outputs/forcing/nwm/` compatibility paths. See
  [project layout](docs/project_layout.md).
- Keep domains, streams (`baseline`, `nrt`, `retro`) and temporal resolutions
  distinct. Forcing outputs use
  `forcing/outputs/<domain>/<stream>/<resolution>/YYYY/MM/`.
- Hourly forcing files contain calendar-day **00–23 UTC** records, or a validated
  contiguous midnight prefix for the newest NRT day. Daily model/forcing
  reductions follow **01–00 UTC interval-end records**. PRISM's 12–12 UTC
  constraint windows must not become production file grouping. See
  [time conventions](docs/nwm_time_conventions.md) and
  [forcing summaries](docs/forcing_temporal_summaries.md).
- `.LDASIN_DOMAIN1` hourly collections have no `.nc` suffix. Daily/monthly
  summaries are different products, not model-ready hourly forcing. Preserve
  variable-specific reductions, units, time bounds and monthly temperature
  extrema semantics from the summary documentation.
- Forcing and model grids must align, but their masks need not be identical.
  Preserve the documented active-cell coverage/static-envelope policy; do not
  redesign masks, CNRFC precipitation handling or donor distances casually.
- NRT must extend without waiting for PRISM or MRMS Pass 2 when a valid fallback
  exists. GFS northern fallback is required for HRRR-selected hours, not opt-in.
  See [NRT scheduling](docs/nrt_operational_extension_schedule.md),
  [GFS operations](docs/nrt_gfs_operations.md), and
  [forcing workflow](docs/forcing_production_workflow.md).

## Model changes and task-specific references

- WRF-Hydro source/build: `external/wrf_hydro_nwm_public-v5.4.0/`; current runners
  use `build-intel/Run/wrf_hydro_NoahMP.exe` within it. Do not overwrite the shared
  executable during active campaigns. See [build guide](docs/wrf_hydro_build.md).
- Model output/restart changes require a small-domain test and a warm-restart
  continuation check before broad production. Preserve daily LDASOUT and both
  hourly/daily CHRTOUT modes, hourly record stacking, and monthly checkpoints.
  See [archive recovery](docs/chrtout_archive_recovery.md) and
  [CONUS production](docs/conus_retro_simulation.md).
- CNRFC work: [domain boundary](docs/cnrfc_domain_boundary.md),
  [forcing subsetting](docs/cnrfc_forcing_operations.md), and
  [restart subsetting](docs/nwm_restart_subsetting.md).
- Coverage/status: `bin/report_forcing_status.py`; see
  [status semantics](docs/forcing_status_report.md). Filename coverage, hourly
  timestamps, completeness and scientific validity are different checks.

Keep code, tests and current operational documentation synchronized. Preserve
unrelated worktree changes and historical evidence; do not treat old experiment
scripts or results as current operational instructions. Commit/push when requested;
exclude generated data, credentials, executables and runtime logs from commits.
