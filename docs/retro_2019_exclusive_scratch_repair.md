# January/March 2019 baseline repair

Submitted September 22, 2026 UTC using `slurm/repair_2019_baseline_exclusive.sh`:

- **4620836**, task 0: January 6 pilot on one exclusive compute-128 node.
- **4620837**, tasks 1–9, concurrency two: January 7, 8, 11, 13, 14 and
  March 19–22. Requires `afterok:4620836`; invalid dependency cancels the follow-up.

Each task requests 12 CPUs but exclusively allocates a 128-CPU node, runs four
assembly workers, and has a four-hour limit. At most two follow-up nodes are used.
The `--tmp=240000` setting is a node-capacity filter, **not** a consumable per-job
scratch reservation. Exclusive allocation prevents competing scheduled jobs on
the same node. Scratch bytes/inodes are logged every 30 seconds.

Earlier retries repeatedly packed ten tasks on awr-2-08 (about 295 GiB scratch).
Remapping succeeded, then temporary NetCDF coordinate writes failed with HDF
errors. Scratch exhaustion is strongly suspected, not yet experimentally proven.
Success requires the production/aggregation/domain-repair commands to finish and
the final baseline manifest and domain-repair attributes to pass acceptance.
Logs: `forcing/logs/baseline-exclusive-<array>_<task>.out`.

These jobs only repair the ten baseline days. Publication of the 17 affected
PRISM-constrained retro days remains a subsequent step; it is not submitted here.

## Baseline acceptance and PRISM follow-up

All ten baseline tasks completed with publication checks passed, in approximately
18–19 minutes each. Logged node scratch usage peaked at 32–34 GiB per task.
Ten such jobs together exceed the roughly 295 GiB node capacity, supporting the
scratch-exhaustion diagnosis.

Array **4623867** subsequently submits two exclusive-node PRISM batches in parallel:
task 0 covers January 5–15, task 1 March 18–23. All 17 dates and their adjacent
baseline/PRISM inputs passed planner eligibility checks. The validated chunk
writer and final static-envelope publication checks remain enabled.
Job **4623868** depends on success of both tasks and audits all 652 days from
2019-01-01 through 2020-10-13, writing the block's `.accepted.json` only if passed.
Original failed controller state remains historical evidence. Cleanup is deferred.
