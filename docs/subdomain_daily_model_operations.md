# Daily NRT model operations by domain

The daily forcing controller supports CNRFC, B120 and Yampa model replays.
UCRB remains excluded until its retro simulation and NRT catch-up are accepted.
Each model job depends on its domain's successful forcing synchronization, the
CONUS summary refresh, and the daily forcing controller. Models run independently
after their own dependencies pass; a model-submission error in one new domain
is reported without suppressing the other domain's submission.

The configurations are `config/<domain>_nrt_daily.json`. CNRFC retains 64 MPI
ranks; B120 and Yampa use 32. Each job requests 120 GB scratch and a two-hour
limit on ordinary `shared-128` resources. `mpan` never requests NRTRES.
The existing daily cron entry drives these follow-ups; no additional crontab
entry or installation is needed. Six-hourly forcing updates do not launch models.

## Publication and activation

Before enabling a new domain, promote its accepted NRT catch-up results to
`nwm/outputs/<domain>/nrt/{hourly,daily}/YYYY/MM/` and paired checkpoints to
`nwm/restarts/<domain>/nrt/YYYY/MM/`. Run logs keep their campaign identities.
B120 promotion uses `bin/promote_yampa_nrt.py --domain b120 --execute`; its
audit-only invocation must pass first. Exact original metadata, file identities
and post-promotion audit results are retained in
`nwm/status/b120/nrt-promotion/`. No NetCDF data are rewritten by promotion.

The generalized runner remains `bin/run_cnrfc_daily_cycle.py` for compatibility:
use `--domain b120` or `--domain yampa`, with `--snapshot-forcing`.
Each new domain requires a completed promotion and its own accepted warm-start
gate. A two-day `--test` run resumes its second day from a daily checkpoint and
compares eight land/routing state variables with the continuous result.
It tests overlapping-hour publication in a private hardlinked mirror. Original
outputs remain unchanged. Only a complete test with `rst_typ=1`, matching domain
and mask, and passed daily-checkpoint continuation can activate that domain.
The configuration's `daily_acceptance` records that exact test report.

## Replay and restart retention

The runner selects the newest available forcing collection's midnight as the
endpoint, checks the required forcing through that hour, and rewinds 11 days.
It chooses a paired 00 UTC checkpoint at or before the rewind. A publication gap
extends the rewind as necessary. Initially only monthly checkpoints may exist,
so the first daily replay can cover most of a month; subsequent runs can use
daily checkpoints. The replay pins forcing file versions before simulation.

Fully coupled warm starts use `rst_typ=1`, `RSTRT_SWC=0`, `GW_RESTART=1`, no t0
output, daily-resolution LDASOUT, and hourly/daily CHRTOUT. Hourly CHRTOUT files
group calendar-day records; daily reductions use 01–00 UTC interval-end values.
Every replay day produces a paired 00 UTC restart. Retention is still plan-only:
monthly anchors and existing checkpoints are preserved until cleanup is approved.

Model locks, once-per-UTC-day submission journals, workspaces and publication
transactions are separate per domain. Publication preserves overwritten files
as recoverable backups and rolls back ordinary failures. An incomplete journal
blocks subsequent publication. Model jobs for the same domain defer new daily
submissions. The daily status is
`nwm/status/<domain>/nrt-daily/latest.json`; job-specific evidence is
`nwm/runs/<domain>/nrt/cycles/job-<ID>/cycle.json`.

## Initial acceptance runs

B120 **5069197** and Yampa **5069198** passed October 1–3, 2026 at 00 UTC using
32 ranks each. Both completed the daily-checkpoint continuation comparison,
published 14 files into private mirrors, and retained two paired daily
checkpoints with no forcing revisions after snapshot capture. Their exact
cycle reports are pinned in the enabled domain configurations. Routine hooks
are enabled for the next daily forcing cycle; no extra cron entry is required.
The first production replay will bootstrap from the existing monthly checkpoints.

B120 promotion preserved 195 days (April 18–October 30, 2026 at 00 UTC), 4,680
hourly channel records and all paired restart states. Its pre/post audits match.
The full regression suite passed with **586 passed, three skipped**.

## B120 retro archive promotion

Job **5070067** audits and promotes the completed `production_v1` retro archive
from January 2, 1979 through April 18, 2026 at 00 UTC. The target paths are
`nwm/outputs/b120/retro/{hourly,daily}/YYYY/MM/` and
`nwm/restarts/b120/retro/YYYY/MM/`. Runs remain under
`nwm/runs/b120/retro/production_v1/`.

The entry point is `slurm/promote_b120_retro.sh`, using four CPUs for metadata
and archive-receipt checks. Every annual acceptance, monthly interval, expected
output date, hourly archive identity/record count and endpoint restart pair is
checked before same-filesystem directory renames. The promotion holds both the
retro campaign and NRT model locks. It preserves metadata originals, rewrites
current run references (including NRT's inherited retro checkpoints), and
rechecks publication after the move. No model rerun or NetCDF rewriting is
needed. No restart files are removed.

`nwm/status/b120/retro-promotion/promotion.json` is the authoritative completion
journal; `plan.json` records exact file identities and moves. The model runner
uses canonical retro paths after completion and refuses an incomplete promotion.
The submission above alone does not establish completed publication.

Yampa uses the same audit and promotion procedure via
`slurm/promote_yampa_retro.sh`, submitted as **5070071**, for January 2, 1979
through April 18, 2026 at 00 UTC. Its targets are
`nwm/outputs/yampa/retro/{hourly,daily}/YYYY/MM/` and
`nwm/restarts/yampa/retro/YYYY/MM/`; logs retain `production_v1` under runs.
The completion journal and original metadata are retained under
`nwm/status/yampa/retro-promotion/`. Yampa and B120 use separate locks and
directories, so their promotions can run concurrently. No model or forcing
data are regenerated, and all restart states are preserved.
