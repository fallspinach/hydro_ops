# Operational CNRFC forcing propagation

The [expanded-grid promotion](cnrfc_grid_promotion.md) replaces the original
grid under the same canonical `cnrfc` name. During migration, forcing/model
submissions are gated; only explicit validation jobs can run. After acceptance,
the hooks below use the new `cnrfc_masks.nc` without crontab changes. The old
grid's bulk data are retired after the promoted synchronization/replay tests.

CNRFC forcing is an exact crop of the published CONUS forcing, retaining the
domain forcing mask and checking completeness on the distinct model mask. No
interpolation, hydrologic simulation or temporal reduction is performed here.
Hourly, daily and monthly products retain the CONUS timestamps, time bounds,
variable meanings and filenames under `forcing/outputs/cnrfc/{nrt,retro}/`.
Only published streams are synchronized; baseline intermediates are excluded.

## Automatic follow-ups

The daily NRT cycle also propagates all three temporal resolutions to the other
approved subdomains. After CONUS summary refresh, the branches are
`CONUS → CNRFC → B120` and `CONUS → UCRB → Yampa`. CNRFC retains its existing
submission hook; `hydro_ops.forcing.subdomain_sync` submits the other three jobs.
Each uses 32 allocated CPUs, eight workers and 120 GB requested scratch.
The worker discovers the latest parent collection at execution, including a
partial newest day, and copies available daily/monthly summaries without
recomputing them. Accepted unchanged subsets are skipped.

Child jobs require successful completion of their parent job (`afterok`). Prior
subset writers for the same domain are serialized with `afterany`, and the
existing domain output lock is retained. Invalid dependencies cancel the new
child job rather than leaving it pending indefinitely; the next daily sweep
can retry. Submission journals under `forcing/status/subdomain-sync/` preserve
each daily chain and prevent duplicate submissions. An uncertain sbatch outcome
requires inspection before retry. Controller submission errors are explicit in
the cycle status and return a failure exit code; already submitted CNRFC model
work remains independent of failures in the other branches.

Per-domain job reports are under `forcing/status/<domain>/nrt-catchup-JOB/` and
include source domain, endpoint, published/skipped/failed counts and elapsed
time. This extension applies to daily NRT cycles only. Six-hourly and monthly
retro scheduling retain their existing behavior. Daily B120 and Yampa model
replays now follow their own subset jobs; see
[daily model operations](subdomain_daily_model_operations.md). No new
crontab entry or installation is needed.

The initial four-domain integration chain uses summary refresh **5069087**,
CNRFC **5069088**, B120 **5069089**, UCRB **5069090**, and Yampa **5069091**.
Its journal is `forcing/status/subdomain-sync/daily-5069087.json`. All five jobs
completed successfully. Summary refresh took 25 seconds; CNRFC, B120, UCRB and
Yampa took 4, 24, 45 and 10 seconds respectively. CNRFC skipped all 491 unchanged
files; B120 published 16 and skipped 475; UCRB and Yampa each published 18 and
skipped 473. Every domain reported zero failures, with hourly coverage through
the October 30 collection. Daily summaries reached October 29 and monthly
summaries September 2026. The regression suite passed with **582 passed, three
skipped** before this integration run.

- Six-hourly latest-hour submissions queue an NRT all-resolution catch-up
  after the extension job ends, including partial UTC-day files and any daily
  or monthly summaries published since the previous cycle.
- Daily latest-hour submissions queue an immediate NRT hourly subset job after
  the extension job ends, without waiting for the longer revision workflow.
- Daily revision completion queues an **NRT-only**, all-resolution catch-up
  after the CONUS daily/monthly summary refresh ends. The NRT model replay
  requires this job to succeed, alongside the controller and summary refresh.
- A separate retro all-resolution catch-up is submitted afterward, serialized
  behind NRT synchronization. It discovers manually produced files and retries
  missed retro subsets/summaries. Its execution or submission failure does not
  block the NRT model. Historical errors remain visible in their own reports.
- Monthly-retro controller completion queues a retro all-resolution synchronization.
- There is no separate fixed-time CNRFC cron job: dependency-chained follow-ups
  wait for their producers rather than assuming they finish by a particular hour.

Follow-ups use `afterany`: if a producer fails after publishing some valid files,
the synchronizer can still propagate those and retain older valid subsets for
the rest. It does not manufacture missing CONUS products or certify that a failed
parent cycle completed. New CNRFC jobs depend on existing CNRFC sync/backfill jobs;
a shared output lock additionally prevents concurrent subset writers. Subsetting
does not delay completion/publication of the CONUS latest-hour product.

The hooks are active through the existing cron commands without crontab edits.
The previously proposed 07:15 UTC catch-up row has been removed from both saved
cron files. If it was installed manually, remove that row on the existing cron
host; the four existing scheduled commands need no changes. No live crontab was
installed or modified here.
Use `bin/render_crontab.py` to render paths after relocating the project. Do not
install duplicate schedules on both login nodes or overwrite unrelated cron entries.

## Incremental publication and safeguards

`bin/submit_cnrfc_sync.py --stream all` submits a 32-CPU, 8-worker job with
120 GB requested scratch and a 12-hour limit. Use `--stream nrt` or `retro`, and
optionally `--frequency hourly|daily|monthly`, to restrict the scope.

The worker checks source inode/size/modification time, domain mask SHA256,
and accepted output identity. Unchanged outputs are skipped without rereading
their full arrays. Legacy receipts receive one full output checksum comparison
before recording their output identity; this makes the first catch-up slower
than later cycles. This identity fast path is not a substitute for periodic
bit-rot audits. Mask hashes are cached per worker only while their file identity
remains unchanged.

Changed/new files are cropped and audited on node scratch, transferred with
checksum verification, and atomically published. Existing outputs with
unrecognized audit provenance are not overwritten. Source replacement races
receive up to three attempts with refreshed identities; other failures remain
visible and are retried by a later sweep. This gives per-file consistency and
eventual synchronization, not an atomic transaction across all three resolutions.

Active-cell completeness is required for the eight hourly meteorological fields
or the required daily/monthly summary fields. Ancillary spatial diagnostics and
QC flags are cropped/masked and checked for exact retained-value agreement, but
may retain missing values from the source. In particular, missing
`gfs_fallback_qc` means unknown legacy provenance; it is not evidence of missing
weather and must not be converted to zero (which means known no action).
Receipts distinguish `active_completeness_fields` from all `validated_fields`.

Reports are under `forcing/status/cnrfc/sync-JOB/status.json` and
`results.jsonl`; submission/dependency manifests are `submission-JOB.json`.
The latest completed report for each selection is
`latest-sync-STREAM-FREQUENCY.json`. Logs are `forcing/logs/cnrfc-sync-JOB.out`.
Inspect `failed` and individual result errors, not just whether submission worked.

### October 22, 2026 diagnostic-validation repair

Four daily all-stream sweeps failed on 45 retro hourly files dated March 5–April
18, 2026, due to missing `gfs_fallback_qc` legacy provenance. This was an
overly strict ancillary-field check, not evidence that the meteorological fields
were missing. The corrected check retains missing ancillary values and still
rejects missing active-cell meteorology. The daily dependency split above
prevents retro failures from blocking NRT simulations.

Full regression suite: 524 passed, 2 skipped. NRT sync **4971578** passed in
5 seconds (475 unchanged accepted products; zero failures). Separate retro
repair/audit sweep **4971579** was submitted to revisit the rejected files;
inspect its job-specific report for publication results. Blocked model job
**4962949** was cancelled and replaced by **4971580**, dependent only on the
fresh NRT sync because controller **4962146** and summary refresh **4962946**
were already verified completed. The daily submission record preserves these
replacement details; no cron schedule or model configuration changed.

Initial operational NRT catch-up: **4658201**, all three resolutions. This is a
normal synchronization run, not a completed acceptance claim; inspect its report
after it finishes. Targeted tests cover serialized submission, change detection,
source-race retry, corruption repair, resolution filtering and cron rendering.
