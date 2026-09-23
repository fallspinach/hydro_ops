# September 2026 NRT catch-up recovery

The September 22 daily cycle published September 14–20 successfully, but failed
to publish September 1–13 after bounded retries. Two separate problems occurred:

- Legacy baseline acceptance required a `day` field and exactly 24 source-file
  entries. Source-aware NRT receipts instead enumerate native inputs (95 entries
  for the inspected files). Thus accepted September 13–14 archives were rebuilt
  unnecessarily, then aggregation failed because the daily destination existed.
- The optional `precip_timing_source_id` field was normalized only for new retro
  production, not NRT PRISM windows. Mixed legacy/current inputs failed schema
  validation. Inspection also found September 1–2 baseline archives unaccepted
  under the CNRFC/domain policy; those need genuine regeneration, not relabeling.

`baseline_publication.accepted_baseline` now provides a shared planner/worker
gate: exact 24-hour timestamps, CNRFC policy where applicable, legacy domain
acceptance or source-aware policy plus a passed identity-matched receipt.
Existing unaccepted archives trigger a full hourly rebuild and explicit atomic
daily replacement, rather than reusing stale hourly files or repeatedly failing
on the existing destination. Valid source-aware baselines are left unchanged.
PRISM window assembly always normalizes the optional timing provenance in the
output: existing IDs are preserved, absent IDs become zero/unknown. Source files
and physical precipitation fields are not modified by schema normalization.

Thirty-one focused tests passed, including receipt identity/policy rejection,
existing-unaccepted archive rebuild selection, both archive writers, source
schema normalization, and NRT-to-retro handoff tests.

## Recovery jobs (September 23 UTC)

- **4623979**, two exclusive-node tasks: rebuild September 1 and 2 baseline.
- **4623980**, dependent on both baseline tasks passing: two exclusive-node NRT
  PRISM batches, September 1–7 and September 8–13. Standard per-day publication
  validation remains required. Submission is not completion or acceptance.
- Successful September 14–20 NRT products are not in the repair scope.

The 1986 NWM job **4524571** is held (`JobHeldUser`), retaining its original
`afterok:4524570` dependency. The running 1985 job was not interrupted; later
years stay behind 1986. Do not release the hold until the planned directory
migration and model forcing-read checks are complete, or the user requests it.
No layout migration has been performed.
# Follow-up: canonical baseline schema

The September 23 cycle exposed a second mixed-schema boundary: September 14
legacy baselines lacked the five GFS/native-donor diagnostics present on
September 15. See [baseline schema compatibility](forcing_baseline_schema.md)
for the versioned archive schema, virtual legacy normalization, and isolated
full-size test. This supersedes timing-field-only compatibility for complete
eight-field baselines; it does not rewrite historical files.
