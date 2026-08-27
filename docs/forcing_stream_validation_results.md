# NRT and retrospective forcing validation results

This log records promotion evidence for the test plan in
`docs/forcing_stream_validation_plan.md`. Outputs and machine-readable reports are isolated under
`outputs/forcing/validation/` and are not operational products.

## Gate A: accepted

Gate A passed 16 of 16 natural-boundary, controlled-availability, expected-failure, and
input-layout checks on 2026-08-26. The authoritative ledger is
`outputs/forcing/validation/reports/gate-a-ledger.json`.

- Stable PRISM routing produced a `retro` result for 2026-02-24; the following day routed to
  provisional `nrt`.
- NLDAS-2/HRRR transition days passed, including an HRRR fallback day after NLDAS-2 ended.
- Removing MRMS Pass 2 selected Pass 1; removing both MRMS passes selected Stage-IV; hiding the
  Stage-IV archive selected realtime Stage-IV.
- NLDAS-only and HRRR-only views produced complete accepted files.
- Removing every non-precipitation source failed cleanly and published no final or partial file.
- Consolidated-daily and hourly source layouts produced exactly equal scientific fields.
- One PRISM reconciliation case required 80 rather than 40 maximum iterations. The accepted
  fraction improved from 0.005251 to 0.003451 without measurable wall-time cost, so 80 is now the
  production default while all scientific tolerances remain unchanged.

## Gate B: partially accepted

The 2026-08-12 through 2026-08-21 NRT window was produced across the NLDAS-2/HRRR boundary. The
three final missing days completed in 8 minutes 26 seconds to 8 minutes 37 seconds using three
concurrent 12-CPU tasks. An unchanged 15-day scheduler scan then reported zero eligible updates.
Independent full-file validation accepted all ten days with no issues. The NRT boundary portion of
Gate B is therefore complete.

The 10-day PRISM stable/provisional boundary window is 2026-02-20 through 2026-03-01. Missing
unconstrained baselines use no more than three concurrent daily tasks because seven-way
concurrency previously caused substantial shared-filesystem contention. Stable/provisional
routing across this retrospective boundary remains the unfinished portion of Gate B.

## Pending gates

Gate C requires a complete recent NRT month and corresponding stable retrospective month. Gate D
requires a 7-14-day unattended scheduled soak. These gates still control formal operational
promotion; the user-authorized retrospective baseline recovery is recorded separately below.

## Long-term retrospective production recovery

The first 2020-12-02 through 2021-12-31 baseline array (`4440538`) completed 220 of 395
calendar-day tasks. A trial increase from 252 to 504 CPUs overloaded node-local scratch I/O:
174 tasks failed, predominantly with CDO/NetCDF HDF write errors, and one final task exceeded the
memory associated with a 12-CPU allocation. Sixteen failed dates retained incomplete hourly sets;
no `.part` files remained, but this demonstrated that hour-at-a-time publication was insufficient
for daily atomicity.

Recovery changes stage all 24 hours until the complete day succeeds, restore static cell corners
for both archive and realtime Stage-IV, allow precipitation products and MRMS quality to vary by
hour within a daily remapping batch, and provide selective missing-day arrays. Material but finite
source relative-humidity excursions can be clipped to physical saturation while setting both the
clipping and invalid-input QC flags; strict rejection remains available to callers. Retry tasks
request 16 CPUs for additional memory and initially run at two-way concurrency. Full-file
validation remains a separate post-production operation so submission does not reopen every large
NetCDF file.

The representative five-day retry (`4442553`) passed every failure class in 10 minutes 12 seconds
to 12 minutes 51 seconds per day. The selective recovery array (`4442588`) then completed all 170
remaining missing or incomplete days with zero failures. It ran four concurrent 16-CPU tasks,
averaged 11.66 minutes per day, and finished in 8 hours 21 minutes wall time. The complete
2020-12-02 through 2021-12-31 baseline now contains 395 complete days, 9,480 hourly LDASIN files,
9,480 manifests, and no abandoned day-part files. A subsequent missing-only scan returned zero
eligible days.

Post-production scientific QC uses a streaming hourly-day validator so all eight fields can be
scanned without creating duplicate consolidated files. It checks all 24 timestamps, grid and
variable schemas, physical ranges, paired wind masks, source provenance, manifests, and staging
remnants.

A 12-day sample spanning seasons, year boundaries, and every repaired failure class passed 12 of
12 full-grid scans. Full scan job `4444940` was then submitted for all 395 days. As of the
documentation checkpoint on 2026-08-27, 285 contiguous days through 2021-09-12 had completed and
all 285 were accepted with zero reported issues. The validator had read 2.65 TB and checked more
than 73 billion finite values per forcing field. Observed extrema remained inside the configured
physical guards: temperature 225.76-325.63 K, specific humidity 0.0000033-0.02832, pressure
57,663-104,789 Pa, wind components -20.11 to 27.81 m/s, shortwave 0-1,368.06 W m-2, longwave
81.28-522.66 W m-2, and precipitation rate 0-0.17681 kg m-2 s-1. Mean validation time was 1.67
minutes per day. The scan was still active, so this is strong partial evidence rather than its
final acceptance ledger; update this paragraph when the remaining 110 reports finish.
