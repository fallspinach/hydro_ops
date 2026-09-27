# Operational CNRFC forcing propagation

CNRFC forcing is an exact crop of the published CONUS forcing, retaining the
domain forcing mask and checking completeness on the distinct model mask. No
interpolation, hydrologic simulation or temporal reduction is performed here.
Hourly, daily and monthly products retain the CONUS timestamps, time bounds,
variable meanings and filenames under `forcing/outputs/cnrfc/{nrt,retro}/`.
Only published streams are synchronized; baseline intermediates are excluded.

## Automatic follow-ups

- Six-hourly latest-hour submissions queue an NRT all-resolution catch-up
  after the extension job ends, including partial UTC-day files and any daily
  or monthly summaries published since the previous cycle.
- Daily latest-hour submissions queue an immediate NRT hourly subset job after
  the extension job ends, without waiting for the longer revision workflow.
- Daily revision completion queues a catch-up across **both NRT and retro**
  and all three resolutions after the CONUS daily/monthly summary refresh ends.
  This also discovers manually produced files and retries missed subsets,
  including retro summaries generated outside the controller.
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

Reports are under `forcing/status/cnrfc/sync-JOB/status.json` and
`results.jsonl`; submission/dependency manifests are `submission-JOB.json`.
The latest completed report for each selection is
`latest-sync-STREAM-FREQUENCY.json`. Logs are `forcing/logs/cnrfc-sync-JOB.out`.
Inspect `failed` and individual result errors, not just whether submission worked.

Initial operational NRT catch-up: **4658201**, all three resolutions. This is a
normal synchronization run, not a completed acceptance claim; inspect its report
after it finishes. Targeted tests cover serialized submission, change detection,
source-race retry, corruption repair, resolution filtering and cron rendering.
