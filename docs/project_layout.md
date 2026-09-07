# Forcing and NWM project layout

The repository operates two coupled production systems. `forcing` owns acquisition and production
of meteorological forcing; `nwm` owns WRF-Hydro/NWM domains, runs, state, and model products. A
forcing product is immutable input to an NWM run and is referenced rather than copied.

```text
forcing/
  inputs/                         external NLDAS-2, HRRR, MRMS, Stage-IV, and PRISM archives
  static/                         source grids, elevations, and reusable remapping weights
  outputs/
    conus/{baseline,nrt,retro}/   daily 24-record LDASIN collections
    validation/                   forcing validation products
  work/                           manifests, task lists, locks, and temporary coordination state
  logs/                           forcing acquisition and production logs
  status/                         machine-readable operational status

nwm/
  static/                         versioned domains, hydrofabric, and parameters
  inputs/                         observations and other model-specific dynamic inputs
  runs/<domain>/                  experiment and cycle workspaces; large transient work uses scratch
  outputs/<domain>/<stream>/      published model history products
  restarts/<domain>/<stream>/YYYY/MM/
                                  paired, verified 00 UTC land and routing checkpoints
  logs/                           WRF-Hydro/NWM logs
  status/                         domain inventories and model-operation status
```

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
