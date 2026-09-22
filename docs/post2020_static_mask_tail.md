# Early post-2020 static-mask follow-up

The last CNRFC rebuild array (4549027, 130 tasks) completed on September 21,
2026. Campaign-wide inventory found 308 earlier retro files without the final
static-envelope manifest: 37 in 2020 and 271 in 2021, scattered between
2020-10-14 and 2021-10-19. This is separate from rerunning precipitation
remapping or PRISM reconciliation.

`bin/repair_post2020_static_tail.py` implements a narrow follow-up:

1. Inspect every candidate's actual NetCDF policy attributes and 24 calendar-hour
   timestamps. Require accepted PRISM reconciliation, existing CNRFC correction,
   an older rectangle or active-gap v3 filling policy, and a verified original manifest. Freeze
   source inode/size/mtime plus policy metadata. Unexpected counts or policies
   stop submission for review.
2. Stage each file and sidecar on node scratch, verify the copy checksum, then
   call the existing approved static-mask writer in private staged-rebuild mode.
   Preserve all retained/active values and ancillary fields. Use chunk integrity
   verification, with full readback for untrusted inputs and month-start samples.
3. Check original identity again before publication. Checksum-verify the final
   transfer and bind its audit/manifest to the permanent file identity. Preserve
   CNRFC/PRISM metadata. No source acquisition, precipitation recomposition,
   temporal redistribution or PRISM rerun is performed.
4. Run an independent final campaign inventory after all array tasks terminate,
   including failed tasks. Require matching publication identities and accepted
   masks, and unchanged CNRFC policy metadata. Report failures rather than infer
   completion from SLURM exit codes alone.

Campaign location: `forcing/work/post2020-static-tail-20260921/`.
All 308 candidates passed inspection (35 rectangle-v1 and 273 active-gap-v3).
Repair array **4601289** completed all 22 tasks successfully. Final audit
**4601290** passed on September 21, 2026, verifying **308 files with zero failures**.
`inspection.json` records all checked files; `tasks.jsonl` freezes 14-file batches;
`submission.json` records worker and final-audit job IDs; `audit/` contains per-day
publication audits; `acceptance.json` records the passed final result. This final
inventory checks publication identities and policy/acceptance metadata; full or
chunk-integrity field checks are recorded in the per-file repair audits.

The repair uses at most four concurrent tasks, two separate writer processes per
task (eight files concurrently), 12 allocated CPUs and 120 GB reserved scratch
per task. Each file has at most two attempts. A failure leaves that task failed
and is identified in the final inventory. No current 2017–2020-10-13 producer,
1981–1985 summary job, NWM simulation, or layout migration is changed.
