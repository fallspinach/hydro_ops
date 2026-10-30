# USGS daily streamflow inventory

`bin/collect_usgs_streamflow_inventory.py` builds a versioned inventory of
**USGS daily mean discharge**: parameter `00060`, statistic `00003`, computation
period `Daily`. Both current and historical records are retained, including
multiple/secondary time series. No arbitrary recent-data or minimum-record-length
filter is applied. Site types are retained rather than assuming that every
discharge record is an unregulated river gauge.

The inventory uses the modernized
[USGS Water Data v1 API](https://api.waterdata.usgs.gov/ogcapi/v1/), not legacy
WaterServices. The `combined-metadata` collection supplies station and series
metadata in one server-side join. Its `Daily values`/discharge/mean selection is
cross-checked against the complete, independently paginated
`time-series-metadata` selection for daily mean discharge. Unequal series-ID
sets, conflicting station metadata or unexpected filter results fail acceptance.
See the [USGS migration guide](https://api.waterdata.usgs.gov/docs/ogcapi/migration/).

## Geographic scope

The master inventory selects USGS locations in the contiguous 48 US states and
DC using provider country/state codes, not a latitude/longitude rectangle.
Alaska, Hawaii, territories and foreign locations are excluded and counted.
It is a CONUS observation inventory, not a list restricted to active NWM cells.
Locations without valid point coordinates remain in the master table with an
explicit issue; they cannot enter a geographic domain list.

Four derived lists use **unbuffered reporting polygons**, not the 20-km model
support buffers or forcing masks:

| List | Static domain | Reporting layer |
| --- | --- | --- |
| CNRFC | `cnrfc` (expanded grid version `cnrfc_hu18_v1`) | `cnrfc_hu18_union` |
| B120 | `b120` | `b120_union` |
| UCRB | `ucrb` | `ucrb_reporting` |
| Yampa | `yampa` | `yampa_reporting` |

The station point must be covered by the reporting polygon; boundary points
are included. Each list records its exact GeoPackage SHA-256 and layer. These
are station-location selections, not proof that the upstream drainage area is
fully inside the domain, a station-to-NWM reach match, or a screening for flow
regulation/diversion. No foreign-network inventory is inferred for the Mexican
part of the CNRFC reporting region.

## Time and record semantics

Native `time_zone_abbreviation` and `uses_daylight_savings` are retained.
The USGS `time-zone-codes` catalog supplies standard/daylight UTC offsets and
names, stored alongside each station and as a separate preserved catalog.
Missing/unrecognized codes are reported, not guessed from longitude or state.
These fields are not silently converted into IANA time zones, historical DST
rules, or UTC daily aggregation bounds.

Each time series preserves the full provider metadata, including its UTC
`begin`/`end` strings, units, primary flag, parent-series ID, description,
sublocation, thresholds and modification timestamp. Record endpoints do not
prove continuous coverage, nonmissing daily observations, or approved values.
No start/end dates are shifted to UTC calendar days. A subsequent observation
downloader must preserve original daily labels, approval/qualifier information
and returned timestamps before defining comparisons with NWM's 00–00 UTC days.
Observed USGS discharge is not generally interchangeable with CDEC full natural
flow, particularly below reservoirs or diversions.

## Execution and products

Run from the project root:

```bash
source bin/project_environment.sh
sbatch slurm/collect_usgs_streamflow_inventory.sh
```

The serial crawler requests four CPUs and a two-hour limit on ordinary shared
resources, uses retries/backoff (including HTTP 429), follows API-provided next
links, and rejects duplicate feature IDs or pagination loops. Existing site
base Python supplies GeoPandas/GDAL; the launcher derives it from the configured
environment, as in the domain builders. Optional `USGS_API_KEY` is supplied in
the request header only, never recorded in URLs or reports. No new package or
credential is installed by this workflow.

```text
observations/static/stations/usgs/streamflow/<UTC timestamp>/
  stations.json, stations.csv, stations.geojson
  time_series.json, time_series.csv
  time_zone_codes.json
  domains/{cnrfc,b120,ucrb,yampa}/
    stations.csv, stations.geojson, station_ids.txt, time_series.csv
  acceptance.json
observations/inputs/usgs/streamflow_inventory/<UTC timestamp>/
  <collection>-<page>.json.gz
  <collection>-<page>.json.gz.source.json
observations/status/usgs/streamflow-inventory/job-<job>/status.json
observations/status/usgs/streamflow-inventory/latest.json
```

Raw responses retain retrieval time, URL and SHA-256; output checksums and
boundary checksums are in the job-specific acceptance report. A snapshot lock
prevents duplicate writers. Interrupted snapshots can be resumed with
`sbatch slurm/collect_usgs_streamflow_inventory.sh --resume <snapshot-directory>`;
cached query URLs/checksums are revalidated. A changing live inventory can cause
the independent series cross-check to fail; inspect the mismatch rather than
publishing an incomplete list. A fresh snapshot is appropriate if cached pages
span incompatible provider revisions. Only passed snapshots update `latest.json`.

Job **5050891** was submitted October 28, 2026. Submission is distinct from
inventory acceptance. This step acquires metadata only: no streamflow time
series download, NWM reach association, or cron installation is included.

### Accepted initial snapshot

Job **5050891** passed in 74 seconds. Snapshot
`observations/static/stations/usgs/streamflow/20261028T181712Z/` cross-checked
**25,911** national daily-mean-discharge series and retained **24,364 CONUS
locations / 24,514 series**. Exclusions were 1,378 series outside CONUS and 19
reported by agencies other than USGS. Every retained location has a recognized
time-zone code; two lack valid coordinates and remain explicitly flagged in
the master inventory, outside all four spatial lists.

| Reporting domain | Locations | Daily-mean-discharge series |
| --- | ---: | ---: |
| CNRFC (expanded) | 2,719 | 2,735 |
| B120 | 846 | 849 |
| UCRB | 1,236 | 1,239 |
| Yampa | 93 | 93 |

Counts include historical locations and other discharge-reporting site types
such as canals, ditches and springs. The master contains 23,082 `ST` stream
locations; `site_type_code` is available for subsequent validation screening.
These are not counts of currently operating, unregulated, or continuously
reporting gauges. Accepted snapshots cannot be resumed/overwritten; use a new
snapshot for updates.

A post-publication audit verified all 22 export checksums, all 54 preserved
source-response checksums, CSV/JSON row and time-zone consistency, and exact
spatial membership for all four lists. B120 station IDs are a subset of CNRFC,
and Yampa station IDs are a subset of UCRB. The full software suite passed
558 tests (three skipped); reporting-edge/no-buffer/missing-coordinate geometry
checks also passed in the site's geospatial environment.

## Model-period selections (1979 onward)

The all-history snapshot remains unchanged. Derive a filtered inventory with:

```bash
source bin/project_environment.sh
"$HYDRO_OPS_PYTHON" bin/filter_usgs_streamflow_inventory.py --since 1979-01-01
```

The default source is the latest passed snapshot; `--snapshot <directory>` pins
a specific one. All source export checksums must match. The main lists retain a
station if at least one daily-mean-discharge series has a provider UTC record
endpoint **on or after January 1, 1979** (inclusive). Older series are omitted
from the selected series list. Existing time-zone fields, DST flags, UTC
endpoint strings and station metadata are unchanged. This is an availability
metadata screen, not a download/verification of nonmissing daily observations
or proof of continuous coverage since 1979.

Stations with no confirmed overlap but an unknown endpoint are exported
separately under `unknown_record_end/`, including their own domain lists. Do
not silently classify them as either pre-1979 or confirmed usable records.
`--unknown-end keep` instead produces a conservative potential-overlap list;
the `-confirmed` selection is the recommended model-period inventory.

The accepted initial selection is:

```text
observations/static/stations/usgs/streamflow_selections/
  20261028T181712Z-since-19790101-confirmed/
    stations.{json,csv,geojson}, station_ids.txt
    time_series.{json,csv}, acceptance.json
    domains/{cnrfc,b120,ucrb,yampa}/...
    unknown_record_end/...
```

| Region | All-history locations | Confirmed 1979+ overlap | Unknown overlap, separate |
| --- | ---: | ---: | ---: |
| CONUS | 24,364 | 17,484 | 754 |
| CNRFC | 2,719 | 1,873 | 49 |
| B120 | 846 | 608 | 9 |
| UCRB | 1,236 | 817 | 15 |
| Yampa | 93 | 63 | 0 |

There are **6,126** CONUS locations with all advertised series ending before
1979. The main selection contains 17,588 overlapping/unknown-end series at the
17,484 confirmed-overlap locations. Source acceptance and output checksums are
recorded; accepted selections refuse overwrite. No new API requests are needed.
The earlier sibling selection without the `-confirmed` suffix retains unknown
overlap in its main lists and is not the recommended confirmed inventory.

## NWM-mapped daily discharge backfill

The user supplied `feature_gage_row.csv` (columns `feature_id,gage_id,row`). Its
7,994 unique gage/reach mappings contain 7,647 numeric USGS-format IDs and 347
other alphanumeric network IDs. Exact string matching, preserving leading zeros,
selects **7,495 confirmed-overlap stations** (7,527 retained series) for download.
No station/reach rematching or reinterpretation of the third-column row index is
performed. The crosswalk is user-provided, not independently validated against
the model hydrography by this step. Eight mapped stations have unknown periods,
14 ended before 1979 and 130 numeric IDs are absent from this inventory; these
are not silently included. Retained mapped reporting-region counts are CNRFC
469, B120 67, UCRB 237 and Yampa 18.

Submit from the project root, supplying the crosswalk location explicitly:

```bash
source bin/project_environment.sh
"$HYDRO_OPS_PYTHON" bin/submit_usgs_discharge.py \
  --selection observations/static/stations/usgs/streamflow_selections/20261028T181712Z-since-19790101-confirmed \
  --mapping /path/to/feature_gage_row.csv
```

The submitter verifies the inventory/selection checksums, preserves an exact copy
of the crosswalk, and pins station metadata, reporting memberships, reach IDs,
all advertised daily-mean series for each selected station, and request bounds.
Requests cover **1979-01-01 through the UTC-yesterday date label at submission**;
the provider endpoint is not clipped to potentially stale metadata end dates.
Only this approved 7,495-station cohort is accepted without reviewing changed
inputs. The download plan is immutable and checksummed.

The pipeline submits a live-data smoke gate (samples from all four domains,
historical/recent/multiple-series cases), then an `afterok` backfill array with
eight shards and at most **two concurrent tasks**. Each task uses two CPUs on
ordinary `shared-128` resources; backfill tasks have eight-hour limits, while
smoke/finalization have two-hour limits. An `afterany` finalizer requires all
shards and all 7,495 station receipts to pass before publishing complete-cohort
diagnostics. This is not an NRTRES workflow. Existing model/forcing jobs and cron
configuration are unchanged.

All shards share a five-second minimum request-start spacing (at most about
720 initial requests/hour), with HTTP 429/server-error retries and backoff. An
optional `USGS_API_KEY` goes in the request header, never reports or URLs. Daily
requests use up to **50,000 records/page**, as supported by the v1 API, and follow
every `next` link. Most station histories fit a single page. Raw pages and query
receipts are compressed/checksummed and reused on retry. Interrupted unreceipted
pages are preserved as orphans and re-fetched. Each station has a publication
lock and an accepted checksum manifest. One bounded second acquisition attempt
handles transient failures. A failed campaign can be safely retried with:

```bash
"$HYDRO_OPS_PYTHON" bin/submit_usgs_discharge.py --resume-plan observations/status/usgs/discharge/backfill-<timestamp>/plan.json
```

Retry refuses a concurrent active download campaign and skips unchanged accepted
stations. A new/different plan does not overwrite station histories automatically:
incremental update/refresh semantics require separate implementation and review.

### Record and diagnostic semantics

The v1 daily `time` field is a **provider date label**, not a UTC instant. It is
stored unchanged as `provider_date`; no midnight timestamp or DST-adjusted UTC
bounds are invented. Station metadata retains native time-zone code/DST flags.
Raw value strings, observation/series IDs, units, approval status, qualifiers and
modification timestamps are retained. Numeric discharge is additionally stored
in cfs and m³/s. Missing/nonfinite/nonnumeric values are null numerically; explicit
`INVALID`, `MISSING` and `REJECTED` qualifiers do not count as valid. Other numeric
qualified values (including estimates), provisional values and negative flows
are retained and separately counted rather than silently discarded. The v1 API
uses nulls for missing values; legacy sentinel substitutions are not inferred.
See [USGS migration](https://api.waterdata.usgs.gov/docs/ogcapi/migration/) and
[efficient querying](https://api.waterdata.usgs.gov/docs/ogcapi/efficiency/).

All returned daily-mean series at a station are preserved separately. Station
coverage diagnostics use advertised primary series only when present; otherwise
the nonprimary-only fallback is explicitly labeled. Multiple series on the same
date count at most once. Differing primary numeric values on a date are flagged
as ambiguous and excluded from the station's valid-day count, not averaged.

Diagnostics include first/last valid dates, completeness and longest missing gap
over that observed span, separate full-request-period completeness, annual
coverage (including leap days), approval/estimate/negative-value counts, conflict
counts, years with data and full calendar years meeting 90% completeness.
The observed-span denominator does not penalize a recently established station
for years before its first observation or a discontinued station after its last.
It also cannot detect undocumented outages outside those observed endpoints;
first valid observation is not claimed to be an official commissioning date.
Partial first/last calendar years are identified by explicit bounds. No screening
threshold removes observations or stations during this backfill.

```text
observations/inputs/usgs/discharge/daily/<campaign>/<USGS-ID>/
  page-0000.json.gz, page-0000.json.gz.source.json, ...
observations/processed/usgs/discharge/daily/<USGS-ID>/
  history.csv.gz                all returned series, one row per series/date
  station.json                  timezone, reach mapping, pinned series metadata
  diagnostics.json
  annual_completeness.csv
  acceptance.json, plan.json, write.lock
observations/status/usgs/discharge/backfill-<timestamp>/
  feature_gage_row.csv, plan.json, submission-<timestamp>.json
  smoke.json, shard-0.json, ...
  station_completeness.csv, annual_completeness.csv
  domains/<domain>/station_completeness.csv
  status.json
```

The finalizer revalidates accepted output/raw-page checksums and checks complete
cohort coverage. Failed/partial campaigns must not be represented as complete.
Routine daily acquisition and cron installation are intentionally deferred until
the completeness results and a suitable recently-reporting update cohort have
been reviewed. Historical discontinued records remain available for validation.

The initial mapped-cohort campaign is
`observations/status/usgs/discharge/backfill-20261028T185506Z/`, requesting date
labels January 1, 1979 through October 27, 2026. Submitted October 28:
**5051086** (seven-station smoke), **5051087** (eight-shard backfill, concurrency
two), and **5051088** (final acceptance/completeness diagnostics). The pinned
crosswalk SHA-256 is
`fbb81046f465b41ebf2491040b3d583261b0c8cf5ef918c176e4d5d64f0ab1f3`.
Before submission, 14 focused tests passed (one optional geospatial test skipped);
the full suite passed 567 tests (three skipped), with Ruff, shell syntax and diff
checks passing. The seven-station gate passed in **32 seconds**, publishing
79,083 source-verified records; the first two backfill shards then started.
This is not yet a declaration of complete acquisition or final diagnostics.
