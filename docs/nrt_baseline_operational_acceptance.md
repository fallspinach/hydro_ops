# Combined NRT writer operational acceptance

Job **4559177** completed successfully on 2026-09-18 (SLURM elapsed 2:15:54).
It produced isolated September 14–15, 2026 CONUS NRT outputs using actual source
availability, with no source-selection overrides. Both target days used HRRR with
GFS northern fallback for all 24 hours and received PRISM daily constraints.

Both adopted writer profiles executed successfully: four supporting baseline days
and two final calendar days used the compressed-chunk path, including intermediate
PRISM windows. Normal publication checks passed. The unchanged repeat preserved
both baseline and final file identities.

| Measurement | Result |
| --- | --- |
| Cold worker production | 8,107.2 seconds (2:15:07) |
| Launch to first-cycle publication, including queue/dependency wait | 8,149.6 seconds (2:15:50) |
| Unchanged repeat | 43.4 seconds |
| Target hours with GFS northern fallback | 48 |
| Functional acceptance | Passed |
| Cold production under one hour | No |

This validates combined writer compatibility and reuse, not the sub-hour latency
goal. The test built fresh private baselines from existing source archives; it does
not establish source-download latency, seven-day throughput, or incremental update
speed when some baselines already exist. A realistic changed-source incremental
test is the next performance measurement. Cron installation is not verified here.

Evidence: `forcing/work/nrt-combined-operational-test-20260918T135200/acceptance.json`.

The dependent automatic finalizer (4559178) was canceled before execution to allow
review of the subsequent mandatory-GFS and NRT-to-retro handoff changes. Those
guards were tested separately; this job is not evidence for their execution.
