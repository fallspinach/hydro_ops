# Cron environment on AWARE login nodes

Use `bin/run_cron.sh` for each entry in `cron/hydro_ops.crontab`. Keep the schedule
installed on **one login node only**. On September 28, 2026, the user confirmed
manual installation of the updated schedule on **login1**. This is a user-reported
deployment confirmation, not a cross-host audit. Ensure no duplicate schedule
remains on login2. Future edits to the saved files still require reinstallation.

## User accounts and operational reservation policy

The Unix execution account determines the deployment context:

| Account | Purpose | NRTRES policy |
| --- | --- | --- |
| `mpan` | Prototyping, development, benchmarks and operational-cycle tests | Do **not** request `NRTRES`, even to work around long queue waits. |
| `cw3ehydro` | Real operational production | Explicitly activate `NRTRES` for time-critical NRT work; prefer ordinary resources for monthly retro work. |

Slurm listing `mpan` among a reservation's permitted users is technical access,
not authorization under this project's operating policy. The earlier suggestion
to use `NRTRES` as a last resort for `mpan` is withdrawn. Do not substitute another
operations-only reservation as a workaround.

Current submissions run as `mpan` without an NRT reservation. No automatic
account-based reservation selection has been implemented by this documentation
change. Before deploying as `cw3ehydro`, explicitly configure and verify
reservation use for the operational compute jobs, including jobs submitted by
controllers and dependent follow-ups; changing the crontab owner alone does not
ensure that nested submissions request it. Also verify environment paths,
credentials, filesystem permissions and single-host scheduling for that account.
Do not run overlapping prototype and operational writers against the same outputs.

## Wrapper and scheduling

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
entries. The saved UTC schedule runs daily at **02:30**, with extension-only cycles
at **08:30, 14:30, and 20:30**. Daily includes the extension, so do not add a second
six-hourly invocation at 02:30. Status reporting remains every two hours at minute
30; retrospective promotion runs at **18:00 UTC on the 18th of each month**.
The monthly cycle is non-urgent and should use ordinary resources rather than
NRTRES when possible, including its dependent jobs. Current `mpan` submissions
do not request NRTRES at all. This preference must also be preserved when
configuring the future `cw3ehydro` deployment; do not apply a blanket reservation
setting to every cycle. The 18th gives a buffer after PRISM's approximate
mid-month updates, not a guarantee that every daily grid is stable; normal
stable-data eligibility checks still apply. The later UTC hour avoids the
early-day workload.
See [NRT scheduling](nrt_operational_extension_schedule.md) for the timing rationale.
Do not add a duplicate source-only refresh schedule.
Observe the first live cycle and its dependent jobs after installation; successful
environment tests are not an end-to-end production validation.
