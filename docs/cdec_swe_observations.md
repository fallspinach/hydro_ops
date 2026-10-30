# CDEC SWE station and sensor metadata

This inventory discovers **all stations exposed by CDEC for sensor codes 3 and
82**, across reporting durations and station status, before acquiring any SWE
time series. There is no model-domain or geographic filter. The source is the
[official CDEC station search](https://cdec.water.ca.gov/dynamicapp/staSearch)
and each result's `staMeta?station_id=...` page.

## Sensor codes versus measurement method

CDEC labels sensor **3** `SNOW, WATER CONTENT` (search abbreviation `SNOW WC`),
and sensor **82** `SNOW, WATER CONTENT(REVISED)` (search abbreviation `SNO ADJ`).
These codes do **not** distinguish snow course from snow pillow. For example,
3LK lists both codes as monthly manual-entry records, while BKL lists code 3
as event, hourly and daily and code 82 as daily. Provider descriptions,
durations, collection methods, station names and historical notes are retained
to support a later instrument classification. Do not silently rename the
sensor-code lists as course/pillow inventories.

Snow water content/equivalent is also distinct from snow depth. No depth sensor
or other measurement code is included by inference, and no time-series data is
downloaded by this collection step. Units and advertised availability are
recorded exactly as provided; availability is not a verified inventory of
nonmissing SWE observations.

## Discovery and outputs

`bin/collect_cdec_swe_metadata.py` makes unrestricted, active-only,
discontinued-only, event, hourly, daily and monthly searches for each sensor.
It uses the union of these results, preserves every search response, and checks
that each station's metadata actually contains every sensor for which it was
discovered. An empty duration/status search is allowed only when a valid empty
station result table is present; error pages and unexpected pagination fail
closed. Any missing station page or sensor mismatch is reported as a failure,
not silently dropped from a successful list.

Each combined station is fetched once. Raw HTML is compressed and SHA-256
checksummed, with source URLs and retrieval times. Native station attributes,
all sensor rows and historical notes are retained. Missing/invalid coordinates
are explicitly recorded as null with issue fields so historical stations are
not discarded solely for incomplete geolocation; original provider fields
remain available. GeoJSON uses null geometry for such stations.

```text
observations/static/stations/cdec/swe/<UTC timestamp>/
  discovery.json                original station IDs and provenance per search
  stations.json                 complete combined metadata and collection status
  stations.csv                  one row per successfully collected station
  stations.geojson              station points for GIS inspection
  sensor_3_stations.csv          one row per station carrying sensor 3
  sensor_82_stations.csv         one row per station carrying sensor 82
  sensor_3_series.csv            one row per sensor-3 reporting duration
  sensor_82_series.csv           one row per sensor-82 reporting duration
observations/inputs/cdec/swe_station_metadata/<UTC timestamp>/
  search-<sensor>-<filter>.html.gz
  stations/<station>.html.gz
  ...source.json                source URL, retrieval time and checksum
observations/status/cdec/swe-metadata/job-JOB/status.json
```

SWE metadata snapshots are nested separately from FNF snapshots so the FNF
updater cannot accidentally select a SWE inventory as its configured 24-station
metadata. Downloaded/generated inventories remain ignored by Git.

## Execution and acceptance

Job **5042947** was submitted October 28, 2026 via
`slurm/collect_cdec_swe_metadata.sh`: four allocated CPUs, two paced HTTP workers,
four-hour limit and ordinary shared resources. Parser/discovery tests and Ruff
checks passed before submission. Station-wide crawling runs on a compute node.
Other observation and model campaigns are not resubmitted by this command.

The snapshot and job-specific report must say `passed`, with no failures and
collected-station count matching discovery, before treating the lists as
complete. Intermediate CSVs are progress snapshots. Source lists and metadata
are retained even on failures. Resume a failed snapshot after inspecting the
report:

```bash
source bin/project_environment.sh
sbatch slurm/collect_cdec_swe_metadata.sh --resume \
  observations/static/stations/cdec/swe/<UTC timestamp>
```

Resuming reuses checksum-verified cached raw responses and retries absent pages;
an exclusive snapshot lock prevents competing resume writers. Starting without
`--resume` discovers a new independent snapshot. No SWE data backfill or new
cron entry is launched at this metadata stage.

## Completed inventory: October 28, 2026

Job 5042947 passed in **1 minute 41 seconds**. Snapshot
`observations/static/stations/cdec/swe/20261028T075431Z/` contains all **496**
discovered unique stations, with no failures, complete valid coordinates and
330 retained historical notes. All 484 sensor-82 stations are also sensor-3
stations.

| Code | Stations | Monthly series | Daily series | Hourly series | Event series |
| --- | ---: | ---: | ---: | ---: | ---: |
| 3 | 496 | 408 | 142 | 146 | 83 |
| 82 | 484 | 405 | 142 | 0 | 4 |

These counts describe listed station/sensor/duration combinations, not verified
nonmissing SWE observations. All 1,330 listed series use provider units INCHES.
Active and discontinued search memberships can overlap at a station with
different reporting durations or sensor histories; do not sum those searches
as mutually exclusive station classes.

The final local audit checked all 510 raw-response SHA-256 values, exact
agreement between discovery and station metadata sensor membership, and both
station/series CSV row counts. Results are saved in `audit.json` beside the
inventory. The full regression suite passed (546 passed, two skipped), as did
Ruff and diff checks. This snapshot completed the metadata-only stage; the
subsequently authorized SWE acquisition is described below.

## SWE data acquisition and daily updates

The user subsequently authorized acquisition and daily updates for both sensor
codes. `bin/submit_cdec_swe.py` plans work from the latest passed SWE inventory,
pins its SHA-256, and submits ordinary-resource jobs through
`slurm/update_cdec_swe.sh`. It suppresses competing campaigns under an exclusive
submission lock. Data workers also lock each station/sensor/duration directory.
The FNF metadata and acquisition workflow remain separate.

All **1,330** station/sensor/duration series are included at their native
sampling: monthly, daily, hourly and event. Codes 3 and 82 are stored separately.
No instrument type is inferred from code or mixed into the other product.
Initial planning produced **6,414 requests**: 813 monthly histories, 284 daily
histories, 4,483 calendar-year hourly chunks and 834 calendar-year event chunks.
The historical metadata end date is honored for discontinued series; open-ended
series run through today's PST date. Five monthly records have no provider
start date. Those use an explicit **1800-01-01 request floor**, identified in
the plan as `explicit_request_floor_missing_provider_start`; this is not an
inferred first observation date. Actual returned coverage is reported separately.

### Requests and time semantics

Every request has explicit bounds. CDEC's blank Start returned only a short
window in the earlier FNF test. Date-only bounds also showed a one-hour summer
shift for hourly/event queries: a request starting October 26 returned a
record labeled October 25 at 23:00. The downloader therefore pads the query on
both sides and crops using the returned timestamps. End padding includes the
last day's complete records and prevents gaps at annual file boundaries.
Unexpected dates outside the padded envelope fail validation.

As documented by the [official CDEC web service](https://cdec.water.ca.gov/dynamicapp/wsSensorData),
output timestamps are treated as fixed PST (UTC−8), retaining provider date and
observation-date strings and adding UTC equivalents. Missing observation dates
remain blank. Monthly course dates and event sampling are retained; no regular
measurement cadence, averaging interval, or NWM UTC day alignment is inferred.

### Storage and processing

```text
observations/inputs/cdec/swe/sensor_<3|82>/<resolution>/<station>/<sha256>.json.gz
observations/processed/cdec/swe/sensor_<3|82>/monthly/<station>/history.csv.gz
observations/processed/cdec/swe/sensor_<3|82>/<daily|hourly|event>/<station>/YYYY.csv.gz
observations/status/cdec/swe/chunks/<station>/<sensor>/<duration>/<request-id>.json
observations/status/cdec/swe/<mode>-<UTC timestamp>/{plan,status,submission}.json
observations/status/cdec/swe/<mode>-<UTC timestamp>/shard-<index>.json
```

Sparse monthly histories use one compressed file each to avoid tens of thousands
of tiny annual files. Higher-frequency records use annual compressed CSVs.
CSV fields preserve station, sensor, duration, source dates, source values and
quality flags, with `value_inches`, `swe_mm` and `value_status` added. Conversion
is **1 inch = 25.4 mm SWE**; monthly SWE is a sampled state, not a monthly sum.
Zeros are retained. Provider `-9999` is recorded as missing in numeric columns
with the original raw value retained. Other negative numeric SWE values remain
present with `negative_swe` status; no clipping, interpolation, replacement of
code 3 with code 82, or quality-flag filtering is performed.

Each chunk validates response identities, units, date bounds, and conflicting
duplicate timestamps. Raw payloads are compressed, reopened and SHA-256 checked.
Processed CSVs are atomically published, reopened, and checked exactly against
normalized source rows. Changed windows merge into existing files without
removing records outside the window. If the server omits previously published
timestamps inside a refreshed interval, publication fails for review; returned
explicit missing values can replace earlier numeric ones. Empty/all-missing
responses are retained and reported, not claimed as numeric observation
coverage. Snow-course series are sparse, so missing calendar dates are not
filled or interpreted as telemetry failures.

Accepted chunk receipts allow retrying the historical backfill without
redownloading unchanged accepted chunks. `--refresh` requests a deliberate
historical recheck. The finalizer verifies task coverage and all referenced
receipts/file identities before publishing `latest-backfill.json` or
`latest-update.json`. Per-chunk verification establishes faithful processing,
not scientific approval of individual observations or their flags.

### Submission, recurrence and gates

```bash
source bin/project_environment.sh
# Seven code/duration samples, before historical acquisition.
bash bin/run_cron.sh bin/submit_cdec_swe.py --mode smoke
# Initial history or restart after failures (accepted chunks are reused).
bash bin/run_cron.sh bin/submit_cdec_swe.py --mode backfill
# Daily update; this is the cron command after initial acceptance.
bash bin/run_cron.sh bin/submit_cdec_swe.py
# Deliberate historical revision scan, at an operator-chosen interval.
bash bin/run_cron.sh bin/submit_cdec_swe.py --mode backfill --refresh
```

Use `--dry-run` to inspect a plan without submission; active campaigns cause a
skip. Matching metadata smoke acceptance is required before backfill. Daily
updates require accepted initial backfill for that inventory. A new station
inventory therefore requires new smoke/backfill acceptance rather than silently
starting unknown histories from a short lookback.

Initial backfill uses eight array tasks with concurrency **two**, four allocated
CPUs and two workers per task: at most **eight allocated CPUs/four request
workers** during acquisition. Tasks request 24 hours; single update jobs request
four hours. All run on ordinary shared resources without NRTRES. A separate
`afterany` finalizer records failure if an array task fails or is incomplete;
it never treats partial execution as accepted history.

The daily update policy is:

- Monthly: re-fetch the full history, capturing older revisions.
- Daily: refresh roughly **400 days**.
- Hourly/event: refresh roughly **seven days**.
- After missed runs: extend the window to the last checked date so no unchecked
  new dates are skipped. Older daily/hourly/event revisions require a deliberate
  full-history refresh, and are not claimed to be captured by the rolling cycle.

The saved cron and template submit updates at **19:15 UTC daily (11:15 PST)**,
15 minutes after FNF acquisition. This is a practical schedule, not a promised
CDEC release time. The cron command skips while smoke/backfill/update/finalizer
jobs are active. Keep installation on login1 only; the saved additions require
manual reinstallation and have **not** changed the installed user crontab.

### Initial jobs and tests

The first smoke job 5043070 exposed the summer-boundary issue; its two monthly
sample CSVs were moved into that failed campaign directory when switching the
monthly layout, preserving the test evidence. Corrected smoke **5043076** passed
all seven code/duration checks, including sparse/empty response handling.
The full regression suite passed (**551 passed, two skipped**); targeted unit
tests also cover exact SI conversion, sentinels/flags, missing observation
dates, next-day trimming, annual chunks, outage catch-up, merged-window
preservation, duplicate/history-loss guards and unchanged-file reuse.

Backfill array **5043120** and finalizer **5043123** were submitted October 28,
2026. Campaign: `observations/status/cdec/swe/backfill-20261028T081734Z/`.
Submission is not completed history: require final `status.json` to pass and
inspect per-series/chunk numeric coverage. Clean-environment submission dry run,
Ruff, shell syntax and diff checks passed before bulk submission.
