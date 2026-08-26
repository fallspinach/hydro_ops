# NRT and retrospective forcing validation plan

## Purpose

Validate source selection, revision routing, recovery, scientific consistency, performance, and
stream isolation before producing the complete NRT and retrospective archives. Tests use isolated
output roots and read-only symlink views of source data. They must never rename, modify, or remove
the authoritative source archive.

As inventoried on 2026-08-26, NLDAS-2 ends on 2026-08-17; HRRR, both MRMS passes, MRMS quality,
and realtime Stage-IV reach 2026-08-21; stable Stage-IV reaches 2026-08-15; and PRISM reaches
2026-08-24. The approximate 183-day PRISM stability boundary is 2026-02-24. Re-inventory these
boundaries immediately before testing because scheduled downloads will advance them.

## Isolated test layout

Use separate roots so no test can be mistaken for operational output:

```text
outputs/forcing/validation/baseline/
outputs/forcing/validation/nrt/
outputs/forcing/validation/retro/
outputs/forcing/validation/scenarios/<scenario>/
```

Record the Git commit, configuration, source inventory, remapping-weight fingerprints, SLURM job
IDs, wall time, peak memory, and output checksums for every run.

## Phase 1: natural-boundary matrix

Produce one PRISM day for each row. A PRISM day is the 24-hour interval from 12 UTC on the prior
date through 11 UTC on the labeled date, so source availability must be checked across both
calendar days.

| Case | Candidate date | Expected behavior |
|---|---|---|
| Stable retrospective | 2026-02-24 | Eligible only for `retro`; output revision is `stable` |
| Just inside mutable period | 2026-02-25 | Eligible only for `nrt`; output revision is `provisional` |
| Known reference | 2026-07-15 | Reproduce the verified full-CONUS result and performance baseline |
| NLDAS and stable Stage-IV available | 2026-08-15 | NLDAS supplies non-precipitation baseline; stable Stage-IV is eligible |
| Last complete NLDAS boundary | 2026-08-17 | Exercise the final currently available NLDAS hours |
| First post-NLDAS day | 2026-08-18 | HRRR fills unavailable NLDAS hours; realtime precipitation sources remain usable |
| Recent complete radar/model day | 2026-08-20 | Exercise HRRR, MRMS Pass 1/2 and quality, and realtime Stage-IV |
| PRISM present but forcing incomplete | 2026-08-24 | Scheduler skips cleanly until the complete baseline exists |

Re-select dates if the re-inventory moves a boundary. Preserve the logical relationship rather
than the literal date.

## Phase 2: controlled availability matrix

Create read-only symlink views that deliberately omit selected inputs. Run the same wet day where
possible so precipitation differences are measurable.

1. All sources visible: MRMS Pass 2 should be selected where quality permits.
2. MRMS Pass 2 hidden: Pass 1 should replace it without losing otherwise covered cells.
3. Both MRMS passes hidden: Stage-IV should become the preferred eligible radar/gauge product.
4. Stage-IV archive hidden: realtime Stage-IV remains eligible and its restored static cell
   corners support conservative remapping.
5. MRMS and Stage-IV hidden: NLDAS-2 precipitation should fill covered hours/cells.
6. NLDAS-2 hidden: HRRR should provide the complete non-precipitation state and the lowest-priority
   precipitation fallback.
7. One input hour hidden but alternatives present: the day should complete using documented
   fallbacks and record the transition in provenance.
8. One required hour hidden with no alternative: no final or partial daily output may be
   published; a later rerun after restoring visibility must succeed.
9. Hourly baseline versus consolidated-daily baseline: all scientific fields must compare exactly.
10. PRISM variable missing: the constrained scheduler must skip the day without damaging the
    unconstrained baseline.

## Phase 3: revision and scheduler lifecycle

Use isolated output roots and controlled copies with preserved data but adjustable modification
times.

1. Run an `early` NRT day, then present it as `provisional`; verify replacement within `nrt` and
   retention of a valid manifest.
2. Cross the six-month boundary; verify the NRT file remains unchanged and a separate `stable`
   file is created under `retro`.
3. Re-run both schedulers without source changes; both must report zero eligible updates.
4. Make one PRISM input newer than its output; only that day should become eligible.
5. Make one baseline daily archive newer; only dependent PRISM days should become eligible.
6. Remove an output manifest while retaining the NetCDF file; the scheduler must rebuild it.
7. Present a corrupt/zero-length input or output; completeness checks must reject it.
8. Invoke a second scheduler while the same stream has an active array; it must skip duplicate
   submission. The other stream must remain independently runnable.
9. Simulate a failed array element, restore the cause, and verify that the next scan resubmits only
   the failed/missing day.

## Phase 4: multi-day production tests

Run progressively larger windows, stopping at each gate for review:

1. A 10-day NRT fast window spanning the current NLDAS boundary.
2. A 10-day window around the PRISM provisional/stable boundary, routed to both output streams.
3. One complete recent month in `nrt`.
4. The corresponding six-month-old month in `retro`.
5. A 7-14-day unattended cron soak test that observes downloads, baseline publication, NRT
   revision, stable routing, duplicate-job suppression, and recovery from ordinary missing data.

At the measured archive-input rate of about 16 minutes per day on 12 allocated CPUs, four
concurrent daily tasks imply roughly one hour per 15-day window or two hours per 30-day month,
subject to filesystem contention. Measure rather than extrapolate when concurrency changes.

## Acceptance criteria

Every published daily file must satisfy all of the following:

- exactly 24 chronological hourly records on the 3840 by 4608 NWM grid;
- all eight required forcing fields with the expected schema, units, masks, bounds, and fill value;
- no negative precipitation, paired finite wind components, physically guarded humidity and
  pressure, and no positive nighttime shortwave beyond the configured solar tolerance;
- source IDs, quality flags, revision, input paths, weight fingerprints, and manifests consistent
  with the scenario;
- PRISM temperature and precipitation diagnostics accepted under the configured guardrails;
- atomic publication with no abandoned `.part` files;
- exact scientific-field agreement between hourly-input and daily-archive-input paths;
- a no-op scheduler rerun when inputs and outputs are unchanged;
- no NRT file replaced or removed by retrospective publication;
- no task exceeding the two-hour SLURM limit or the approximately 24 GB available to a 12-CPU
  allocation; and
- aggregate throughput adequate for the scheduled concurrency without sustained metadata or
  storage saturation.

## Promotion gates

- **Gate A:** all single-day natural and controlled scenarios pass.
- **Gate B:** both 10-day boundary windows pass with correct source and revision transitions.
- **Gate C:** one NRT month and one retrospective month pass completeness, scientific QC,
  idempotence, and performance review.
- **Gate D:** the unattended soak test has no unexplained gaps, duplicate arrays, partial outputs,
  or cross-stream replacement.

Large-scale production begins only after Gate D. Independent hydrologic validation and calibration
remain a later scientific promotion gate and should be evaluated before calling a forcing version
final for model skill, even though they do not block operational file generation testing.
