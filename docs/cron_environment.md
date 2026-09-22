# Cron environment on AWARE login nodes

Use `bin/run_cron.sh` for each entry in `cron/hydro_ops.crontab`. Keep the schedule
installed on **one login node only**. The user removed the login2 crontab before
the login1 wrapper tests; this change does not install a crontab on either node.

The previous log's fatal error was `FileNotFoundError: 'squeue'`, not a Python
`subprocess` import failure. Cron did not inherit the interactive SLURM module.
The preceding `which: no fi_info` came from the base Conda MPI deactivation hook.
Both symptoms were reproduced with a minimal environment.

The wrapper invokes the hydro-ops Python directly (no `conda run`, activation,
login shell, or `.bashrc`). It explicitly sets the environment's executable path,
AWARE SLURM paths/configuration/library paths, project import path, and single-thread
BLAS/OpenMP limits. It preserves the inherited home directory and credentials.
Startup logs include UTC time and hostname; `exec` preserves the command exit code.
This is a **site-specific login-node launcher**, not the MPI/Fortran runtime setup
for NWM compute jobs; those jobs retain their own module-loading scripts.

The project location is now discovered from the wrapper (or overridden with
`HYDRO_OPS_PROJECT_ROOT`); Python/SLURM defaults live in `config/site.env`, with
optional trusted `config/site.local.env` overrides. Render the schedule for a new
location using `bin/render_crontab.py`. See [project relocation](project_relocation.md)
for remaining script/manifest dependencies and the coordinated maintenance procedure.

From the project root, test without submitting production work:

```bash
env -i PATH=/usr/bin:/bin /usr/bin/bash bin/run_cron.sh --check
env -i PATH=/usr/bin:/bin /usr/bin/bash bin/run_cron.sh \
  bin/update_nwm_forcing.py --cycle six-hourly --dry-run
```

`--check` imports required Python packages, locates CDO/NCO, checks the SLURM
clients, and queries the live queue. It does not submit jobs or validate external
download credentials. The coordinator's dry run can legitimately report a skip
when a conflicting cycle is active. The same checks should be repeated if the
schedule is moved to another host or site modules change.

Review the saved schedule and existing crontab on the chosen host before installing.
Installing a file replaces that host's current user crontab, so preserve any unrelated
entries. The four schedules and UTC cadence are unchanged; only their launcher and
absolute log paths changed. Do not add a duplicate source-only refresh schedule.
Observe the first live cycle and its dependent jobs after installation; successful
environment tests are not an end-to-end production validation.
