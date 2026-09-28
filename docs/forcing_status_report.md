# Forcing status: domain, stream and temporal resolution

Run `python bin/report_forcing_status.py` from the project root. External inputs
have their own table. Published forcing and baseline inventory is grouped into
one table per discovered domain, with separate stream/resolution columns:

```text
Domain: cnrfc
Stream     Resolution First period Latest UTC / period  Periods  Missing        Size
nrt        hourly     2026-03-02   2026-09-27 03:00         ...      ...         ...
nrt        daily      2026-03-02   2026-09-26               ...      ...         ...
nrt        monthly    2026-03      2026-08                  ...      ...         ...
retro      hourly     1979-01-01   2026-03-02 23:00         ...      ...         ...
```

This is an illustrative layout, not a current coverage claim. Domain directories
under `forcing/outputs/` are discovered when they contain baseline, nrt or retro
streams. CONUS retains its default rows even if empty; regional baseline rows
appear only when that stream directory exists. No code changes are required to
report additional domains following the same layout.

## Latest hour and scope

Hourly products remain calendar-day collections. The reporter reads only the
small `time` coordinate in the newest collection for each domain/stream and
prints its actual last timestamp in UTC. It does not assume that a file named
for a day contains 24 records: a partial day ending at 03 UTC is shown as 03:00,
not 23:00. Daily and monthly rows show their period labels, not an invented hour.

Malformed/missing time coordinates, duplicate newest-day files, inconsistent
dates and a file changing during the read produce an `unknown` timestamp and an
attention message. An unreadable newest file is not silently replaced with an
older timestamp. These checks do not validate meteorological arrays, all-hour
continuity, summary freshness or model readiness. Missing counts refer to absent
calendar-day/month files in the selected range, not missing individual hours.
`--start`/`--end` also bound which production file is considered newest.

## JSON compatibility

`--format json` uses schema **1.2**. New consumers should read:

```text
domains[domain][stream][resolution]
```

Hourly entries add `latest_valid_utc` (ISO-8601 UTC or null), `latest_time_status`
(`read`, `missing`, `unknown`), `latest_time_error`, and `latest_file_records`.
Existing `first_day`/`last_day` remain filename-derived date fields. The legacy
`production_streams[stream]` CONUS-hourly view and
`summary_streams[domain][stream][resolution]` daily/monthly view remain available.
External-source fields and the JSON cron command are unchanged.

The cron report will adopt the new layout/schema on its next run; no schedule
change is needed. Reading newest-file time coordinates adds a small bounded
NetCDF read per hourly stream, not a scan of large forcing arrays.
