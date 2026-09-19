# 2011–2012 aggregation recovery

Controller 4524976 failed after 728 of 731 target days were published. Baseline
task 4557315_300 produced all 24 October 29, 2011 hourly files but failed during
daily aggregation with `NetCDF: HDF error`. The log does not establish the
underlying filesystem/resource cause. The missing archive blocked October 28–30
PRISM calendar publication. The repair planner mistakenly counted retained hourly
files as completion even in daily-publication mode and submitted no repair.

`submit_forcing_days.py --missing-only` now requires a verified daily archive by
default. Only explicit `--keep-hourly` permits complete hourly files as completion.
A regression test covers the 24-hour-present/daily-missing case. Convergence now
reports `blocked_no_eligible_prism_tasks` when no task can be submitted, rather
than inaccurately claiming that maximum attempts were exhausted.

Recovery job **4580835** uses 64 CPUs and 240 GB scratch. It aggregates scratch
copies of the October 29 hourly files, runs normal baseline domain repair, produces
only October 28–30 retro files with stable PRISM and static-envelope processing,
then audits all 731 days using the normal block-acceptance checks. Original hourly
files and the original failed controller manifest are preserved; no baseline
cleanup is requested. Other 728 retro publications are not regenerated.

Controller **4524977** now has `afterok:4580835`, replacing its impossible
`afterok:4524976`. Later dependencies remain unchanged. Recovery failure keeps the
chain blocked; submission is not acceptance. The recovery journal is
`forcing/work/retro-2011-2012-recovery-4580835/acceptance.json`.

## Accepted result (2026-09-19)

Job 4580835 completed successfully in 1 hour 8 minutes 31 seconds. The recovery
report records `status=passed`, `baseline_recovery=passed`, and all **731 days**
audited successfully. Controller 4524977 was released and started 2013–2014
baseline production. The original failed controller record remains unchanged as
historical evidence. Baseline and original hourly cleanup was not performed.
