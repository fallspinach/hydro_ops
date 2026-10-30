# CDEC full-natural-flow station metadata

For the separate, station-wide sensor-3/82 SWE inventory, see
[CDEC SWE observations](cdec_swe_observations.md).

The observations subproject starts with metadata for the 24 station IDs in
`config/cdec_fnf_stations.json`. Reproduce the snapshot with:

```bash
source bin/project_environment.sh
"$HYDRO_OPS_PYTHON" bin/collect_cdec_station_metadata.py
```

This is a small metadata-only request (24 official station pages), not a flow
time-series download. The collector uses bounded HTTP retries and sequential,
paced requests. Timestamped directories are never overwritten. A missing or
mismatched station, malformed sensor table, or invalid coordinate fails the
inventory; failed snapshots remain distinguishable by their report status.

## Layout and current snapshot

- `observations/inputs/cdec/station_metadata/<UTC timestamp>/`: original HTML.
- `observations/static/stations/cdec/<UTC timestamp>/stations.json`: complete
  parsed metadata, all sensors, historical notes, source URLs and SHA-256.
- `stations.csv`: station name, coordinates, raw elevation, basin, county,
  hydrologic area, nearby city, operator, maintenance and provenance.
- `sensors.csv`: every sensor listed at the requested stations.
- `fnf_sensors.csv`: only full-natural-flow sensors, including accumulated FNF.
- `stations.geojson`: station points for GIS review; provider coordinates are
  preserved without an inferred datum transformation.

Snapshot **20261028T071314Z** passed: all 24 requested stations and 281 sensor
records. Earlier snapshot `20261028T071300Z` failed during parser development;
its failed report and duplicate raw pages were removed after verifying the
successful snapshot. The parser regression now covers CDEC's unterminated numeric
degree entity, station ID validation, sensor identifiers and historical notes.

Downloaded/generated observations are ignored by Git. Code, configuration and
this documentation remain version-controlled. Future time series should live
under provider-specific `observations/inputs/` and `observations/processed/`;
domain mappings belong under `observations/static/domains/`, with no duplicate
station series per model domain. Model comparisons belong under
`nwm/validation/<domain>/<stream>/`.

## Sensor semantics and cautions

All 24 stations advertise **sensor 65, monthly FLOW, FULL NATURAL, AF**.
Twenty-three advertise **sensor 8, daily FULL NATURAL FLOW, CFS**; **TNL**
does not list daily FNF. Some also advertise **sensor 290, daily accumulated
FNF, AF**. Do not confuse sensor codes with the distinct internal `sensor_no`
plot identifiers; both are preserved. Other flow and forecast sensors are
retained in the full inventory but not classified as FNF observations.

The advertised start/end dates are sensor metadata, not a verified inventory of
nonmissing observations. “Present” does not establish freshness. Timezone and
daily interval conventions still need verification before ingesting time
series. Raw elevation values, including AMF's `0 ft`, are retained as reported,
not treated as surveyed/validated terrain elevations. Station coordinates
need reach-matching review before assigning a model outlet.

FNF is a calculated/naturalized flow product, not automatically equivalent to
measured regulated discharge. Preserve estimated/provisional flags and
calculation changes when evaluating model output. Notable source notes:

- [AMF](https://cdec.water.ca.gov/dynamicapp/staMeta?station_id=AMF): daily FNF
  calculations changed in WY2026 to match monthly calculations.
- [FTO](https://cdec.water.ca.gov/dynamicapp/staMeta?station_id=FTO): daily
  calculation moved downstream of the Thermalito Complex starting WY2024.
- [MRC](https://cdec.water.ca.gov/dynamicapp/staMeta?station_id=MRC): daily
  calculation moved to Merced Falls starting WY2024.
- [PSH](https://cdec.water.ca.gov/dynamicapp/staMeta?station_id=PSH): August 2025
  notes describe historical monthly FNF revisions excluding SQS contributions,
  with different methods for different historical periods.
- [TRF](https://cdec.water.ca.gov/dynamicapp/staMeta?station_id=TRF): historical
  source changes and estimated daily values later replaced with administrator
  data; estimated values omit certain reservoir adjustments.
- [YRS](https://cdec.water.ca.gov/dynamicapp/staMeta?station_id=YRS): October
  2024 notes distinguish FNF/forecast location from sensors relocated to YRE.

All provider notes are retained verbatim in the local JSON/raw HTML for review.
No USGS IDs, drainage areas, sensor coverage or reach associations are inferred
where the metadata does not provide them.

## Daily/monthly FNF records and recurring updates

`bin/submit_cdec_fnf.py` submits the initial backfill and every subsequent
refresh using `slurm/update_cdec_fnf.sh`. The job requests four CPUs (about
8 GB under the site's per-CPU memory policy), two download workers, and two
hours on ordinary shared resources. The submitter checks for active jobs under
an exclusive submission lock; the worker also holds an exclusive dataset lock.
The scheduler and compute worker use `bin/project_environment.sh` rather than
interactive Conda activation. No NRTRES is requested.

```bash
# Show the 47 planned series without downloading or submitting.
bash bin/run_cron.sh bin/update_cdec_fnf.py --plan-only
# Test scheduler wiring without submitting.
bash bin/run_cron.sh bin/submit_cdec_fnf.py --dry-run
# Start or refresh all series; duplicate active work is skipped.
bash bin/run_cron.sh bin/submit_cdec_fnf.py
```

Acquisition covers **23 daily sensor-8 series and 24 monthly sensor-65 series**.
Sensor 290 is an accumulated product and is excluded. TNL daily is explicitly
absent, not synthesized from monthly data. The initial metadata inventory pins
each sensor's historical start date. Requests always set an explicit Start and
End: a live probe found that a **blank Start silently returned only 24 months**
despite the web form's beginning-of-record wording. End is today's date in fixed
PST. A full refresh uses each sensor's entire advertised history, so older
revisions (including the PSH historical corrections) can be captured. If new
metadata extends a sensor's start, the next refresh uses the latest passed
inventory. Metadata collection itself is an explicit separate command.

The [official CDEC web-service form](https://cdec.water.ca.gov/dynamicapp/wsSensorData)
states that dates/times/output use **Pacific Standard Time**. Raw date and
observation-date strings are preserved; normalized timestamps use fixed UTC−8,
with UTC equivalents eight hours later, including summer dates. These labels
are not asserted to be interval-end stamps, and no 01–00 UTC or NWM-specific
aggregation is applied. Monthly date labels (usually the first of the month)
remain month labels. Provider interval semantics need separate confirmation
before daily model/observation comparisons.

Storage is provider/station based, shared across model domains:

```text
observations/inputs/cdec/fnf/<daily|monthly>/<station>/<response-sha256>.json.gz
observations/processed/cdec/fnf/<daily|monthly>/<station>/YYYY.csv
observations/processed/cdec/fnf/<daily|monthly>/<station>/manifest.json
observations/status/cdec/fnf/job-JOB/{plan,status}.json
observations/status/cdec/fnf/{latest,submission}.json
observations/logs/cdec-fnf-JOB.out
```

Raw responses are content-addressed and compressed; unchanged payloads are
reused. CSVs preserve raw values, units, flags, original timestamps and provider
period labels; SI values are additional columns. Daily CFS converts to m³/s
using 0.028316846592; monthly acre-feet converts to m³ using 1233.48183754752.
Monthly volume is not mislabeled as a discharge rate. No smoothing, imputation,
clipping of negative flows, or quality-flag filtering is performed. Observed
provider missing sentinel `-9999` becomes a blank numeric/SI value with an
explicit status; its original raw value is retained. Other negative numeric
FNF estimates remain numeric and are identified as negative for review. Provider
flags, including `e`, `r` and blanks, remain unchanged rather than assigning
unverified meanings.

Each series validates station/sensor/duration/units, date range, duplicate
timestamps and finite numeric conversion. Missing calendar periods, explicit
missing rows, negative values and latest numeric period are reported separately.
History loss is rejected if a refresh omits any timestamp previously published;
the previous series remains in place for review. Explicit returned missing
values can replace earlier numeric values. Annual CSV publication is atomic,
unchanged CSV bytes are not rewritten, and the series manifest is published
after all annual files. The run report separately records each accepted/failed
series; a failed run can leave successful series updated, and is never reported
as complete. Consumers should read accepted series manifests and avoid reading
in the middle of a refresh if they need a consistent multi-year snapshot.

Initial backfill **5042761** was submitted October 28, 2026 UTC. Availability
ranges and numeric coverage must be checked in its job-specific final report;
submission alone is not a completed data inventory.

That first run identified five daily series with occasional missing `obsDate`
fields (KGF, MRC, PSH, SCC, WWR). The parser now retains these dated flow records
with blank observation-date fields; it does not fabricate a timestamp. Rerun
**5042776** repairs those series and rechecks the accepted series. Audit job
**5042790** depends on its success and rebuilds every annual CSV from retained
raw JSON, checking exact bytes and all 47 series. Routine refresh jobs now
include that same audit after acquisition. `audit.json` beside the job-specific
status report must pass separately from acquisition status.

Refresh 5042776 completed successfully in **2 minutes 11 seconds**: 47 accepted
series, zero failures, 232,883 daily rows (650 annual files) and 33,729 monthly
rows (2,832 annual files). Earliest daily data is April 21, 1985; earliest
monthly data is October 1900. Individual stations have their own start dates.
Latest numeric daily dates range October 22–26, 2026; all monthly series reach
September 2026. Recent requested rows can be present with missing values and
must not be confused with numeric coverage.

KGF daily June 13, 1996 is absent: targeted JSON and CSV requests for June
12–14 both omit it. Other expected dates are represented, sometimes by explicit
missing values. Thirty daily records omit `obsDate`; these remain usable based
on `date` with blank observation-date fields. Some `date` and `obsDate` values
differ even when both are present: both are preserved and no alignment is
silently changed. Negative naturalized estimates are retained and need
application-specific review, not unconditional conversion to missing/zero.

Audit 5042790 passed: **all 47 series, 266,612 rows and 3,482 annual CSV files**
matched normalization of the checksum-verified retained raw responses exactly.
The full regression suite passed (544 passed, two skipped); focused parser,
unit/revision, and cron tests passed again after handling absent `obsDate`.
The clean-environment cron submission dry run, Ruff, shell syntax and diff
checks also passed. Acquisition and verification are complete; the saved cron
addition still requires the user's manual installation on login1.

The saved crontab and relocation template submit updates at **19:00 UTC daily
(11:00 PST)**, a practical daytime check rather than a guaranteed CDEC release
time. Installation is separate: reinstall the reviewed saved schedule manually
on login1 only, preserving unrelated entries and retaining no duplicate cron
on login2. The entry logs under `observations/logs/`, and the renderer supports
`@OBS_LOG_ROOT@` when relocating. Cron files were prepared but not installed.
