# Latest-hour NRT scheduling

The existing `bin/update_nwm_forcing.py --cycle ...` entry point now routes NRT
cycles through the validated latest-hour path.
This integration is undergoing its first coordinated production run.
The full pre-submission suite passed (415 tests). First production extension:
**4637147**; daily revision launcher **4637148** depends on its successful
completion. The summary job will be submitted only after revision convergence.

The 128-CPU latest/recent-NRT workers use `compute-128` (override with
`HYDRO_OPS_NRT_PARTITION` when migrating). `shared-128` rejected this allocation
with `QOSMaxCpuPerJobLimit`; lightweight controllers and summaries retain the
project's ordinary partition. The initial rejected submission started no jobs.

## UTC schedule

The saved schedule and relocation template use `CRON_TZ=UTC`:

| Cycle | Submission time (UTC) | Work |
| --- | --- | --- |
| Daily | 02:30 | Latest-hour extension, then source refresh/revisions, then summaries |
| Six-hourly | 08:30, 14:30, 20:30 | Latest-hour extension only |

This gives each 00/06/12/18 UTC boundary a 2.5-hour source-publication buffer.
The daily cycle replaces, rather than duplicates, the 02:30 extension. It targets
completion of the preceding calendar-day forcing file and availability of today's
00 UTC endpoint, needed for the preceding model day and its daily summary.
The extension may publish later available hours too; these are submission times,
not guaranteed completion times or hard data cutoffs.

Do not wait for PRISM, MRMS pass 2, or the newest NLDAS-2 batch when an accepted
fallback is available. The daily revision pass subsequently incorporates preferred
sources and refreshed inputs. Publication delays and SLURM queue times can still
delay availability. Model operations requiring the revised lookback should wait
for hourly revision success, but need not wait for summaries.

UTC scheduling avoids daylight-saving shifts. Status reporting and monthly-retro
timing are unchanged. Editing/rendering these files does not install cron: install
on only one login node after reviewing its existing entries. At this schedule
update, `crontab -l` on login1 reported no crontab for mpan.

## Order of work

- **Six-hourly:** submit one 128-CPU latest-hour worker (16/8/8 internal workers,
  240000 MB scratch). Refresh HRRR and required GFS for missing recent hours;
  use other preferred sources already present locally. Do not wait for PRISM,
  MRMS pass 2, or the broader archive refresh. No summaries are on this path.
- **Daily:** run the same extension first. An `afterok` launcher then calls
  `--cycle daily --revisions-only`, retaining the existing source-refresh and
  revision controller. After that controller succeeds, submit a separate
  16-CPU/8-worker daily/monthly summary refresh. The controller does not wait
  for summaries to complete.
- **Retro:** unchanged.

The end date printed by the existing planner describes the revision window only;
it no longer caps the latest-hour worker. The broad daily revision window and
its existing recent-tail lookback have not been retuned in this change.

## Safety and status

The extension holds `forcing/status/nrt-gfs/cycle.lock`, validates the last
published receipt and its midnight-prefix time axis, and fills forward without
skipping days. Missing new HRRR hours (HTTP 404) stop extension; other network,
coverage or audit errors fail visibly. Required GFS coverage is still audited.
Existing active HRRR downloads defer submission to avoid concurrent canonical
writers. Catch-up exceeding seven days requires an explicit backfill.

`forcing/status/nrt-gfs/latest-extension.json` reports the latest accepted model
hour separately from the existing revision-cycle report. A failure may leave
earlier newly accepted days available, but is not reported as a passed cycle.
SLURM/queue/source-publication latency remains outside the worker benchmark.

`forcing/status/nrt-summaries/latest.json` reports summary refresh success/failure.
The regular `bin/report_forcing_status.py` report now includes daily/monthly file
inventories for every discovered domain's NRT and retro streams, in both terminal
and JSON output. JSON schema 1.1 adds `summary_streams[domain][stream][frequency]`
and `nrt_summary_refresh`, preserving the existing hourly `production_streams`.
Each inventory reports first/last period, counts, bytes, duplicates, partial files,
and gaps (monthly gaps count calendar months). Without `--start`/`--end`, gaps are
only within each inventory's first-to-last existing period, not an estimate of the
backlog relative to hourly coverage. Explicit dates select the audit window;
monthly selection includes the months containing those dates. An empty inventory
without explicit bounds has unknown expected coverage, not certified completeness.
This metadata-only scan does not validate NetCDF contents or aggregation signatures;
`scan.summary_freshness_validated` is false. Failed NRT summary refreshes and detected
summary gaps/duplicates/partial files appear under Attention. The existing two-hour
status cron command automatically includes these fields; no new cron entry is needed.

The summary job scans the available accepted archive to detect missing/stale
outputs, skips unchanged signatures, and atomically replaces changed outputs.
Daily intervals require all 01–00 endpoints; monthly outputs require every day
of a complete calendar month. A changed day-start record invalidates the previous
daily interval and potentially the previous month. An incomplete first month is
omitted, not extrapolated. Failed summaries do not invalidate hourly forcing.

The first summary refresh may also perform a substantial historical backfill.
Subsequent unchanged scans should be inexpensive, but this integration's full
latency must be measured. A summary controller already holding the publication
lock causes a new overlapping summary attempt to fail visibly, not write twice.

## Commands

Use the established cron environment wrapper, or source
`bin/project_environment.sh` before invoking:

```bash
python bin/update_nwm_forcing.py --cycle six-hourly --dry-run
python bin/update_nwm_forcing.py --cycle daily --dry-run
```

Omit `--dry-run` to submit. `--revisions-only` is an internal continuation flag,
not the normal cron entry point. Inspect failed dependencies and reports before
retrying; no crontab is automatically installed by these commands.
