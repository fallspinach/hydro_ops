# Stage-IV late-revision observations

The purpose is to measure actual content changes after seven days, not infer
them from newer server timestamps. Initial September 20–22 comparisons found
87 byte-identical GRIB pairs despite newer publication timestamps. Three days
are not enough to estimate a climatological revision frequency.

`bin/audit_stage4_late_revisions.py` observes calendar days 8, 9 and 10 before
the UTC run date. It downloads available realtime products to a private
`forcing/work/stage4-revisions-<timestamp>/` snapshot, then compares each with
the corresponding operational raw file without modifying either. Use SLURM:

```bash
source bin/project_environment.sh
sbatch --output=forcing/logs/stage4-revision-audit-%j.out \
  slurm/audit_stage4_late_revisions.sh
```

To audit existing private snapshots, use `--as-of YYYY-MM-DD` and
`--candidate-root <Stage-IV data root containing realtime/YYYY/MM/DD/>`.
This mode performs no downloads. Each run records an immutable JSON report in
`forcing/status/stage4-revisions/`, including checksums, paths, reference and
candidate modification times, valid day, age and result:

- `identical_bytes`: no decoded comparison is necessary.
- `encoding_only`: different bytes, but identical decoded variables, masks,
  dimensions and checked units/calendar/grid-mapping attributes. This label
  does not assert that all metadata are identical; global processing metadata
  are intentionally ignored.
- `decoded_changed`: changed values, masks, coordinates or checked schema;
  report per-variable cell counts and maximum finite absolute difference.
- `no_reference`: no retained raw comparator; do not count as unchanged.

Changed byte pairs are converted into isolated scratch NetCDFs for exact
decoded comparison. Input identity changes during a comparison fail the audit
rather than reporting a possibly inconsistent result. Source outages fail visibly.
Snapshots are retained for review; no production data or forcing receipts are
rewritten and no downstream jobs are triggered.

Initial jobs 4708695/4708696 failed because the new report directory was not
created. That initialization bug was fixed and a CLI regression test added.
Replacement jobs: **4708697** audits the retained September 30 benchmark;
**4708698** collects fresh observations. Both passed: 4708697 checked 87 pairs
for September 20–22 in seven seconds; 4708698 downloaded and checked 87 pairs
for September 21–23 in 26 seconds. All pairs were byte-identical, with zero
missing references. The samples overlap and must not be presented as six
independent days. Unit tests cover mask/value changes, schema changes,
encoding-only differences and CLI report initialization. No recurring schedule
was installed by the initial exploration.

## Daily observation campaign

The saved cron file and relocation template now include a daily **18:00 UTC**
submission through **November 30, 2026**. Install the updated crontab on the
single designated host to activate it; editing the file does not install it.
The submitter becomes a no-op after that date. This is an isolated four-CPU
SLURM audit on ordinary resources, not NRTRES and not a forcing dependency.
Active observation jobs are detected before submission; a worker lock also
protects reference capture against overlapping workers.

`--monitor` first captures the current day-seven files into immutable private
references under `forcing/work/stage4-revision-monitor/references/`, with a
capture timestamp and checksums. It then compares day-8–10 downloads against
those references, not operational copies. Existing references are never refreshed;
their checksums are validated before use. The first three days of the campaign
will have missing references for some dates; these are explicitly excluded from
unchanged counts. Missed capture days remain missing rather than being falsely
labeled as day-seven observations. Ages are UTC calendar-day offsets, not a
claim that all records were sampled at exactly 168 hours.

Daily reports and raw snapshots are retained. No automated deletion or change to
production acquisition/revision policy is enabled. Review unique valid days/hours
and missing references after a month, then decide whether to continue to the
two-month endpoint. The campaign currently supplies per-run JSON evidence;
cross-run statistical summarization is a separate analysis step.

After acceptance, a daily observation for one or two months would establish a
more useful sample. Summarize unique valid hours and days separately by 1/6/24-hour
product, and distinguish new late content from an older reference that was
already stale before day seven. Repeated age-8/9/10 comparisons of the same hour
are not independent samples. For a strict after-day-seven revision rate, retain
a known day-seven snapshot and compare the subsequent versions against it.

Production adoption is separate: unchanged source content should preserve the
existing canonical NetCDF identity, while genuinely changed inputs must invalidate
the appropriate baseline, PRISM windows and neighboring publications. The daily
controller currently actively revises a recent seven-day window; extending source
acquisition alone does not extend that revision coverage. Do not change this
policy or suppress revisions merely because this exploratory sample is unchanged.
