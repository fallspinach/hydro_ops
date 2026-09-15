# NLDAS-2-based forcing-mask stability study (2026-09-12)

Rollout update: the same approved v4 mask is now enabled for new post-2020
CNRFC/PRISM rebuild submissions, after both corrections and active-cell repair.
Canary job 4520188 gates replacement campaign 4520189. The original 1979–2002
archive sweep remains unchanged; previously completed post-2020 outputs need a
separate clipping-only pass. See the
[post-2020 retry record](forcing_production_workflow.md#post-2020-retry-and-static-envelope-rollout-2026-09-12)
for scope, acceptance gates and job coordination.

## Purpose and scope

Explore a static clipping mask for previously overfilled forcing, without rerunning
remapping or PRISM reconciliation. Requirements: preserve every active NWM cell
inside the literal NLDAS-2 rectangle (25–53 N, 125–67 W), preserve reasonable
inactive-water coverage, and remove unnecessary offshore nearest-neighbor values.
The initial study changed no production forcing, policy, or running production
jobs. The subsequently approved clipping campaign is documented below.

Native audit job 4519348 and target-grid audit job 4519349 completed successfully.
Reproduction utilities: `bin/study_forcing_masks.py` and
`bin/plot_forcing_mask_study.py` (the latter uses Pillow).

## Sampling and evidence

- Native NLDAS-2: January/April/July/October 15 of every year 1979–2026 through
  July 2026: 191 daily files, no missing samples, all 24 records and eight variables.
  Coordinates were checked for exact equality. All 36,672 variable-hour masks
  were identical: the same 80,439 valid native cells. Union minus intersection: zero.
- NWM: 12 daily files, hours 00/06/12/18, eight variables (384 variable-hour masks).
  Samples are freshly reconstructed January–April 15, 1979 baseline and monthly
  PRISM candidates from `forcing/work/restore-water-v3/`, plus March–June 15, 2021
  daily-PRISM retro files. Source headers identify NLDAS-2 and v3 coverage.
  Target coordinates were checked against the model mask with absolute tolerance
  1e-5 degrees. All 10,315,371 active cells had data in every sampled field/hour.
- Previously rectangle-filled v1 outputs are unsuitable for deriving original
  inactive-cell support: the legacy fill erased that distinction. The 1979 fresh
  candidates avoid this problem, and exact agreement of the meteorological masks
  with fresh 2021 reconstructions supports the cross-period comparison.

Full paths, record indices, counts, and exact packed-mask SHA-256 hashes are in:

```
forcing/work/coverage-study-20260912/native_summary.json
forcing/work/coverage-study-20260912/target_summary.json
forcing/work/coverage-study-20260912/native_masks.npz
forcing/work/coverage-study-20260912/target_masks.npz
forcing/work/coverage-study-20260912/candidate_masks.png
```

## Results on the NWM grid

Counts below are inside the NLDAS-2 rectangle and include active-cell gap filling.

| Fields | Valid cells | Stability in target samples |
| --- | ---: | --- |
| T2D, Q2D, PSFC, LWDOWN | 10,820,773 | Identical across fields, hours, and periods |
| U2D, V2D | 10,840,392 | Identical across hours and periods |
| SWDOWN | 10,840,392–10,860,309 | 19,917 fringe cells vary; identical union/intersection across periods |
| RAINRATE, 1979 baseline | 10,859,362 | Stable across sampled hours/months |
| RAINRATE, 1979 monthly PRISM | 10,859,442 | Stable; adds 80 cells relative to baseline |
| RAINRATE, 2021 daily PRISM | 13,730,174 | Entire rectangle valid in sampled output |

Shortwave processing explicitly sets nighttime values to zero in
`src/hydro_ops/forcing/radiation_wind_hour.py`; finite shortwave coverage therefore
need not equal raw interpolation support. Precipitation validity also cannot be
treated as proof of native meteorological support: the modern output mask covers
the whole rectangle. This study records numerical validity, not donor distances
or proof that every inactive value is independently observed.

## Candidate static mask

The promising compromise is:

```
NLDAS2_rectangle AND (NWM_active OR union_of_seven_meteorological_valid_masks)
```

The seven-field union is exactly identical for 1979 baseline, 1979 monthly-PRISM
retro, and 2021 daily-PRISM retro samples. It also contains every valid 1979
precipitation cell, both before and after the monthly constraint.

| Candidate | Active cells retained | Inactive cells retained | Inactive rectangle cells excluded |
| --- | ---: | ---: | ---: |
| Thermodynamic union | 10,315,371 | 505,402 | 2,909,401 |
| Seven meteorological fields | 10,315,371 | 544,938 | 2,869,865 |
| Unrestricted all-eight union | 10,315,371 | 3,414,803 | 0 |

Thus the seven-field candidate removes about 84% of inactive rectangle cells
relative to blanket filling, while preserving the sampled NLDAS-2-era coverage
including lakes and coastal waters. Do not use an unrestricted union including
modern precipitation: it defeats the offshore-filtering objective.

## Interpretation and next step

The evidence strongly supports a stable NLDAS-2-based envelope, but this is a
seasonal native audit and a limited target-grid study, not an exhaustive audit of
every historical hour/source combination. HRRR-only periods were deliberately
excluded. The candidate is an exploratory array, not a deployed static-grid product.

Before bulk application, freeze a versioned mask with coordinates, active-mask
checksum and derivation metadata, then test clipping copies of representative old
files. Require identical active-cell values, complete active coverage, masked
outside-rectangle values, and matching manifests. Masking cannot repair missing
active values: audit/fill those separately. Clipping old overfilled files also
cannot recover the original donor provenance of retained inactive values; that is
the explicit speed-versus-exact-reconstruction compromise. Do not fill new inactive
holes simply because the static mask permits them. Keep HRRR-only handling separate.

## Copy-only application pilot

At the user's request, job array 4519387 tests the seven-field mask on four full
days: 1979-05-15 (monthly PRISM), 1985-07-15 (daily PRISM, before Stage-IV),
2002-07-15 (Stage-IV era), and 2021-03-15 (modern reconstructed daily-PRISM forcing).
The first three inputs carry the older rectangle-fill policy. The fourth tests
removal of modern precipitation-only coverage. All inputs identify NLDAS-2 as
the meteorological source; HRRR-only cases are not covered.

The frozen, pilot-only mask and separate output copies are under
`forcing/work/static-mask-pilot-20260912/`. The mask NetCDF includes coordinates,
the active mask, content hashes, and study provenance. It has not been deployed
as an operational static-grid asset. The command rejects in-place destinations
and existing output paths. Production sources and their manifests are untouched.

`bin/test_static_forcing_mask.py` rewrites compressed copies in node scratch,
then checks all records of all eight fields. It requires complete source active
cells; preserves active and all retained values byte-for-byte; preserves existing
missing values inside the mask; requires all excluded cells to be missing; and
verifies time, coordinates, and ancillary data after writing. No interpolation or
PRISM calculation is repeated. Source/QC ancillary fields remain unchanged, so
consumers must use forcing-variable validity or the static mask, not ancillary
source IDs alone, to determine usable coverage.

Only verified copies are transferred to permanent test storage, with a complete
file-checksum comparison of scratch and transferred copies. Each copy receives
a `.pilot-audit.json` report (not a replacement production manifest) containing
per-variable hashes, coverage counts, runtime and file sizes. Active-data gaps,
coordinate mismatches or invalid mask checksums fail rather than trigger filling.
Five small-array safety tests cover successful clipping, missing active data,
unsafe masks, coordinate mismatch, and rejection of in-place writes.

### Pilot results

All four array tasks passed. Across 768 variable-hour records, every active cell
was complete and bitwise unchanged; all retained values (including existing
missing values) were unchanged; and no valid forcing value remained outside the
mask. Auxiliary arrays and timestamps passed read-back hashes, and permanent
test copies matched scratch-file checksums. Source file identity, size and mtime
were unchanged during processing. Production files were not replaced.

| Date | Processing plus audit/copy | Original bytes | Pilot bytes | Size reduction |
| --- | ---: | ---: | ---: | ---: |
| 1979-05-15 | 383.4 s | 4,500,261,138 | 4,439,550,366 | 1.35% |
| 1985-07-15 | 346.0 s | 4,526,375,027 | 4,477,341,258 | 1.08% |
| 2002-07-15 | 345.9 s | 4,446,799,881 | 4,398,322,881 | 1.09% |
| 2021-03-15 | 351.6 s | 4,637,998,986 | 4,641,137,523 | -0.07% |

The four tasks ran concurrently on one shared node, each allocated 12 CPUs for
memory headroom and 120 GB scratch reservation; the utility itself is serial.
These are measured pilot runtimes including deliberately thorough audits, not
a multi-node throughput benchmark. Existing level-2 compression was retained.
Masking is primarily a spatial-validity correction, not a large storage saving;
uniform offshore values already compress well, and rewriting can slightly change
file size. The modern sample removed precipitation outside the common envelope
without changing its meteorological values. Existing small inactive holes within
the union remain missing, as intended for a clipping-only operation.

The successful tests support staged application to already complete NLDAS-2-based
archives. Operational adoption still needs manifest/controller integration and
coordination with running rebuilds; the pilots do not change their policy or
constitute authorization to replace the full archive.

## Approved historical archive policy: v4

Following the successful pilots, the user approved historical archive application
on 2026-09-12. The versioned policy is `nldas2_seven_met_static_envelope_v4`.
The immutable mask asset is:

```
forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc
```

It stores `lat`, `lon`, `keep`, and `active`, plus derivation, model path, scope,
study-summary checksum and array checksums. The `keep` checksum (SHA-256 of
contiguous Boolean array bytes) is
`d7ff57e87c3dfe709d3f165abfd289bc2761dc262f1be7379927a9fc1aafb4b5`.
The envelope contains 10,860,309 cells, including all 10,315,371 active cells.
Do not replace this asset with a newly derived mask under the same version.

For existing NLDAS-2-based files, apply the same envelope to all eight fields,
including precipitation. Modern precipitation-only offshore coverage is
deliberately not retained. Require all active cells to be complete before clipping;
preserve every retained value and existing inactive missing value; mask everything
outside the envelope. There is no new nearest-neighbor calculation, PRISM
adjustment, or timestep regrouping. This is an explicit approximation for old
overfilled inactive values: retained donor values are kept, not reconstructed.
The special partial 1979-01-01 file retains its original records unchanged.

This archive postprocessing policy does not silently change the v3 generation
default or the running CNRFC reconstruction code. Future adoption in generation
should be an explicit final clipping stage after active-gap filling, with matching
manifest/certificate handling. Do not run a legacy rectangle-fill repair after
v4 clipping: it would refill excluded cells. HRRR-only operation needs a separately
validated envelope and is rejected by the current application utility.

### Historical campaign 4519428

- Scope: all 8,766 existing **retro** daily files, 1979-01-01–2002-12-31.
  Calendar inventory is complete; this does not assert all content is already valid.
  Leftover baseline files (2,563) and all nrt/post-2020 files are excluded.
- 627 batches, at most 14 days each; eight concurrent tasks, each 12 allocated
  CPUs and 120 GB node scratch reservation, 48-hour task limit. Maximum allocation
  is 96 CPUs. Based on the pilots, nominal elapsed time is about 4.5–5 days at
  continuous eight-worker occupancy, before queueing and I/O contention.
- The separate post-2020-10-14 CNRFC campaign 4517005 is unchanged. Its products
  must receive the envelope in a separately coordinated stage after their CNRFC
  correction, not concurrently with the current rebuilding of the same files.
- Existing spin-up readers may see the old or atomically replaced file; active
  values and all timestamps are identical. There are no historical production
  writers active at launch. The new workers use per-day advisory locks; unrelated
  writers that do not honor those locks must not be started on this archive.

Campaign state, frozen inventory, submission command, per-day audit journals,
prior manifest content and per-batch failure lists are retained under:

```
forcing/work/static-envelope-v4-1979-2002-20260912/
forcing/logs/static-envelope-v4-4519428_*.out
```

`bin/apply_static_forcing_mask.py` reuses the fully audited clipping implementation.
It writes a validated candidate, saves a durable journal, atomically replaces the
daily NetCDF, then updates its manifest with `static_envelope` metadata, current
file identity and checksum. Existing PRISM/source provenance is preserved. Where
an old monthly product has no daily manifest, only the appropriate static audit
metadata is added; a 24-hour source-file history is not invented. No full second
archive is retained: excluded values are removed from the published files after
validation, while source inputs, pilot copies, and audit/provenance are retained.
Recovering removed inactive values would require the source/reconstruction workflow.

A completed file is skipped only when its policy, mask hash, durable audit and
manifest identity match. An interrupted NetCDF/manifest publication can recover
from the durable journal and candidate file checksum. Each worker attempts a day
at most twice, records failures and continues with other days; any unresolved
failure makes that array task fail. Failed array indices can be resubmitted with
the same campaign environment and script: completed days are skipped. This is
bounded retry and resumability, not an unbounded automatic repair of bad source
data. Source active gaps and retired-v2 files are refused and reported for separate
repair/restoration. Six tests cover clipping safeguards plus publication, skipping,
manifest recovery and target-scope enforcement.
