# Explicit baseline cleanup plan

`bin/plan_baseline_cleanup.py` is read-only with respect to forcing data. It has
no deletion option. It creates a JSON inventory listing each eligible baseline
NetCDF and manifest sidecar by absolute path and inode/size/mtime identity.
Each entry names its retro replacement and captures the replacement's identity,
previously audited data checksum, and hashes/identities of the supporting audit
and manifest JSON files.

Checks require stable accepted PRISM, all 24 correctly dated calendar-hour
records, the current static-envelope mask/policy/content audit, matching
publication identities, and the CNRFC policy for dates on/after July 1, 2020.
The planner reuses prior scientific audits; it does not recompute full NetCDF
checksums or revalidate every field value. Any missing/stale/unaccepted
replacement is reported as blocked. The entire report passes only when every
requested day is eligible.

The September 22–23, 2026 plan is:

```bash
python bin/plan_baseline_cleanup.py \
  --start 2003-01-02 --end 2020-10-12 --workers 4 \
  --output forcing/status/cleanup/baseline-20030102-20201012-deletion-plan.json
```

This scope retains January 1, 2003 and October 13, 2020 as boundary buffers. It
does not include NRT-period baseline, final retro files, summaries, source data,
or leftover hourly files. It does not recursively delete directories.

Before any separately authorized deletion, check active and queued consumers,
revalidate the recorded baseline/replacement/evidence identities, and abort any
changed entry. A saved plan is evidence of eligibility at scan time, not a lease
preventing later changes. Keep the manifest and a separate execution journal
after cleanup; baseline files would otherwise require regeneration to recover.

`bin/execute_baseline_cleanup_plan.py --plan PLAN --journal NEW_JOURNAL` performs
identity/evidence preflight only. Explicit `--execute` enables deletion after
all entries pass. It confines targets to the exact dated baseline file and
sidecar, rejects symlinks and changed files, rechecks each entry immediately
before deletion, and writes durable per-day intent/completion records. It never
deletes directories or expands wildcards. Interrupted execution requires review
of the journal; it does not silently resume or skip missing targets.

The approved 2003–2020 plan's execution journal is
`forcing/status/cleanup/baseline-20030102-20201012-deletion-journal.jsonl`.
Only its final `completed` event establishes successful execution.
