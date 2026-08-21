# Hydro Ops

Portable workflows for meteorological forcing, hydrologic model runs, and analysis on SLURM.

## Python environment

The installed environment is not stored in Git. Its portable specification is
tracked in `environment.yml` and uses only conda-forge packages. With Miniforge:
The specification also installs this repository as an editable package, providing the `hydro-ops` command.

```bash
mamba env create --file environment.yml
conda activate hydro-ops
```

After editing the specification, synchronize an existing environment with:

```bash
mamba env update --name hydro-ops --file environment.yml --prune
```

For non-interactive SLURM jobs, use the environment without shell activation:

```bash
/home/mpan/local/miniforge3/bin/conda run --no-capture-output --name hydro-ops COMMAND
```

## Layout

```text
src/       installable Python package and command-line interface
config/    portable TOML defaults and ignored local overrides
slurm/     batch entry points
tests/     unit tests
data/      forcing/<provider>/<product>, observations, static, model_inputs
outputs/   model_runs and analysis products
work/      temporary/intermediate files
logs/      job logs
```

Downloaded data, outputs, work files, logs, credentials, and `config/local.toml` are ignored by Git. Empty directory markers only are tracked.

## Recurring forcing refresh

`bin/update_forcing.py` reports the latest valid time and file count for every maintained
forcing stream, then submits NLDAS-2, Stage-IV, PRISM, HRRR, and MRMS refresh jobs. It
checks SLURM first and skips a workflow when a job with that name is already pending or
running. A non-blocking lock also prevents simultaneous updater processes.

```bash
# Report only.
python bin/update_forcing.py --status-only

# Report and submit missing refresh jobs.
python bin/update_forcing.py

# Preview submissions.
python bin/update_forcing.py --dry-run
```

The canonical project schedule is tracked in `cron/hydro_ops.crontab`, where future cron
jobs can be maintained alongside this one. Copy its reviewed entries into `crontab -e`.
The forcing refresh entry runs every six hours using the project environment:

```cron
0 */6 * * * cd /cw3e/mead/projects/cwp206/agentization/hydro_ops && /home/mpan/local/miniforge3/bin/conda run --no-capture-output --name hydro-ops python bin/update_forcing.py >> logs/update-forcing.log 2>&1
```

The individual workflows retain their configured latency and refresh behavior: NLDAS-2
targets its lagged day, Stage-IV refreshes realtime plus archive data, PRISM checks its
revision window, HRRR retrieves the previous complete UTC day, and MRMS refreshes recent
Pass 1, Pass 2, and quality fields.

## NLDAS-2 setup

The requested archive is NASA GES DISC's NLDAS-2 primary hourly forcing (File A), not a NOAA-hosted archive. It requires a free NASA Earthdata Login account and authorization for the **NASA GESDISC DATA ARCHIVE** application.

Create `~/.netrc` (never put this file in the project):

```text
machine urs.earthdata.nasa.gov login YOUR_EARTHDATA_USERNAME password YOUR_EARTHDATA_PASSWORD
```

Then protect it with `chmod 600 ~/.netrc`. If credentials live elsewhere, set `netrc` under `[nldas2]` in the ignored `config/local.toml`, or export `HYDRO_OPS_NLDAS_EARTHDATA_NETRC`.
The downloader maintains Earthdata's session cookies in `~/.urs_cookies` with mode 600.

Test discovery without network/authentication:

```bash
hydro-ops download nldas2 --date 2026-08-01 --dry-run
```

Download one day interactively or submit it through SLURM:

```bash
hydro-ops download nldas2 --date 2026-08-01
hydro-ops submit nldas2 --date 2026-08-01
```

A date range is inclusive:

```bash
hydro-ops submit nldas2 --start 2026-08-01 --end 2026-08-07
```

With no date argument, the job fetches five UTC days ago because NLDAS near-real-time data typically lag several days. Change `lag_days` in `config/local.toml`. Reruns compare valid local files with server size and modification time, incomplete downloads use `.part`, and up to four files download concurrently by default.

To run daily, add this line with `crontab -e` on a host where cron is supported (adjust the absolute path after migration):

```cron
15 8 * * * cd /cw3e/mead/projects/cwp206/agentization/hydro_ops && /home/mpan/local/miniforge3/bin/conda run --name hydro-ops hydro-ops submit nldas2
```

The submission requests one node, one task, and `download_jobs` CPUs on `shared-128`. Override paths, partition, account, time, lag, or concurrency in `config/local.toml`; see `config/project.toml` for all variables.

## Stage-IV setup

Stage-IV precipitation is available without credentials from two NOAA streams. The
realtime NOMADS stream contains individual GRIB2 files and is refreshed on every run
because files can be revised. The stable archive stream contains daily tar files; valid
local copies are skipped when their size and modification time match the server copy.
Only CONUS products are selected. After each download, every native GRIB2 field is
converted separately with `wgrib2` to preserve that reporting hour's full projected grid
and missing-data mask; products with different accumulation periods are never merged.

```bash
# Refresh the configured seven-day realtime lookback and fetch the archive day.
hydro-ops download stage4

# Select one stream and an inclusive date range.
hydro-ops download stage4 --stream realtime --date 2026-08-20
hydro-ops submit stage4 --stream archive --start 2026-07-01 --end 2026-07-31
```

Data are stored below `data/forcing/noaa/stage4/{realtime,archive}`. With no
date arguments, realtime refreshes today plus the preceding seven days, while archive
fetches the day eight UTC days ago. These values and both server URLs are configurable
under `[stage4]` or with `HYDRO_OPS_STAGE4_*` environment variables.
Converted files are stored below `data/forcing/noaa/stage4/netcdf/{realtime,archive}`.
Existing local files can be converted without contacting NOAA:

```bash
hydro-ops convert stage4 --stream both
```

## HRRR hourly forcing

The HRRR workflow extracts only the native-grid CONUS fields required by hydrologic
models from NOAA's public AWS archive. For every valid UTC hour it retrieves 2-m air
temperature and specific humidity, surface pressure, surface downward shortwave and
longwave radiation, and 10-m U/V winds from the `f00` analysis. Hourly precipitation
comes from the preceding cycle's `f01` APCP record because `f00` APCP has a zero-length
accumulation interval. Index-driven HTTP byte ranges avoid downloading the roughly
150-MB full surface files.

```bash
hydro-ops download hrrr --date 2026-08-15
hydro-ops submit hrrr --start 2026-08-01 --end 2026-08-07
```

Each command processes all 24 hours of every requested UTC date. With no date argument,
the configured `lag_days` selects the previous UTC day. Eight-record subset GRIB2 files
and converted NetCDF files are stored below
`data/forcing/noaa/hrrr/conus/3km/hourly/YYYY/MM/DD`. The source URL, lag, concurrency,
timeouts, retries, destination, and `wgrib2` executable are configurable under `[hrrr]`
or through `HYDRO_OPS_HRRR_*` environment variables.

## PRISM daily forcing

The PRISM workflow downloads CONUS AN daily precipitation and mean, maximum, and minimum
temperature at 4-km resolution directly as NetCDF. Before fetching a grid it queries PRISM's release metadata and compares the
release date and grid count with a JSON record beside the local file. Unchanged grids are
not downloaded. With no date arguments, it checks the previous 215 days through one UTC
day ago, covering PRISM's rolling six-month revision cycle with additional margin.

```bash
hydro-ops download prism --date 2026-08-12
hydro-ops download prism --variable tmean --date 2026-08-12
hydro-ops submit prism
hydro-ops submit prism --start 2026-07-01 --end 2026-08-31
```

NetCDF files are stored under
`data/forcing/oregon_state/prism/an/4km/daily/{ppt,tmean,tmax,tmin}/YYYY/MM`. PRISM ZIP packages and
extracted intermediate files use `/scratch/$SLURM_JOB_USER/job_$SLURM_JOB_ID/prism`
inside SLURM jobs and fall back to the project `work/prism` directory otherwise. Stage-IV
archive conversion uses the same scratch policy. The server request delay, lag, refresh
window, URLs, and destination are configurable under `[prism]`.

## MRMS hourly precipitation

The MRMS workflow downloads the CONUS 1-km `MultiSensor_QPE_01H_Pass1` and
`MultiSensor_QPE_01H_Pass2` accumulations plus `RadarAccumulationQualityIndex_01H` from
NOAA's public AWS archive. Passes remain separate: Pass 1 normally arrives about 20
minutes after the valid hour, while the more gauge-complete Pass 2 arrives about 60
minutes after it. Consumers can select Pass 2 when present and fall back to Pass 1.

```bash
# Refresh yesterday and all eligible hours today; unavailable recent Pass 2 is expected.
hydro-ops download mrms
hydro-ops submit mrms

# Strict archive download for complete UTC dates.
hydro-ops download mrms --date 2026-08-15
hydro-ops submit mrms --start 2026-08-01 --end 2026-08-07
```

Timestamped gzip-compressed GRIB2 sources are retained under
`data/forcing/noaa/mrms/conus/1km/hourly/raw/{pass1,pass2,quality}` and native-grid
NetCDF files under the corresponding `netcdf` tree. With no date arguments, the command
refreshes the configured lookback through the most recent hour expected to have Pass 1;
a later run fills in Pass 2. Source, lookback, products, concurrency, retries, timeouts,
destination, and `wgrib2` are configurable under `[mrms]` or with
`HYDRO_OPS_MRMS_*` environment variables.
