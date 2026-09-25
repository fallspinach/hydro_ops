# NRT efficiency experiment — September 24, 2026

Job **4629922** is an isolated paired benchmark, dependency-ordered after
operational controller **4629401**. It requests **64 CPUs and 240000 MB scratch**,
retaining production's 8 assembly and 4 precipitation-remap workers. It does not
change cron, operational inputs, published forcing, or the active cycle's code.
Prototypes live under `hydro_ops.experiments`, outside the production PRISM cache's
forcing-library fingerprint list. They are not adopted automatically.

The benchmark builds September 20, 2026 baselines twice on current native inputs:
reference first, then a private process-only wrapper that prepares the 30-hour
precipitation window once and shares it across NLDAS/HRRR source segments. Cache
input/static/output identities are checked by the established cache reader.
Both runs must use identical source fingerprints and every variable/cell/mask and
variable attribute must compare exactly. Second-run filesystem warmth is a timing
caveat; this is not a controlled cold-cache experiment.

Next, two scratch copies of the accepted NRT file are audited. The reference
rewrites all eight fields at every hour. The prototype writes only records whose
values actually need normalization/masking. It still checks all active cells,
all hours, required provenance/PRISM acceptance, and reads back every field to
verify no valid values remain outside the envelope. NaN/nonstandard missing
representations must retain the reference behavior. The outputs are compared.

Finally, representative NLDAS and Stage-IV input copies receive a history-only
metadata change. A conservative all-variable/dimension/dtype/attribute/value
comparison measures the cost of recognizing identical content. Only `history`
is ignored; values, units, coordinates, fill conventions and other attributes
must match. This is a publication-guard prototype, **not** permission to ignore
mtime changes in old receipts. If adopted later, it would retain the existing
file and its identity when a freshly downloaded/converted candidate is equivalent.
Real updates would still publish and invalidate dependent products. These trials
do not prove historical Stage-IV refreshes were scientifically unchanged.

Logs: `forcing/logs/nrt-efficiency-4629922.out`.
Receipt: `forcing/work/nrt-efficiency-4629922/acceptance.json`.
The benchmark passed: mixed baseline 1344.58 → 976.17 seconds (27% less),
final audit 199.05 → 108.87 seconds (45% less), with identical fields and masks.
Input comparison took 0.223 seconds for NLDAS and 0.846 seconds for Stage-IV.
The reference-first cache-warmth caveat still applies.

The shared mixed-source precipitation cache and sparse final-audit writes are
now adopted in `nrt_cycle.py`. All field/readback checks remain enabled.
`slurm/test_nrt_efficiency_adoption.sh` exercises the complete PRISM-constrained
cycle and unchanged repeat in private directories, using 64 CPUs and 240000 MB
scratch. The earlier paired benchmark must not be rerun as an old-vs-new timing
comparison: its production reference now includes the adopted optimizations.
Content-aware upstream replacement remains experimental, not adopted.

## Current cycle end date

`bin/update_nwm_forcing.py::cycle_window` sets **both daily and six-hourly NRT
cycles to today UTC minus two days**. On September 24 this is September 22;
native source coverage through September 23 does not advance that hard cutoff.
This experiment deliberately does not change the scheduling window.

A follow-up will replace the fixed publication ceiling with the latest contiguous
usable **hour**, including partial current-day collections. PRISM and MRMS pass 2
must not block a usable fallback. The normal cell-level audit and required GFS
northern coverage remain mandatory. Partial files must never contain placeholder
future hours or claim full-day completeness. Extend them by atomic replacement.

`nrt_freshness.py` implements a tested planning contract that stops at the first
missing primary/GFS hour and labels its result `planned_not_published`. It is not
yet connected to cron and does not certify model readiness. Daily archive,
GFS publication, final audit, source acquisition and model-reader partial-day
behavior must be tested together before removing the fixed cutoff.

Cycle processing now prioritizes unpublished days in forward order, followed by
accepted days newest first, and then older NLDAS replacement backlog. Existing
dependency fingerprints still decide whether a revision needs computation.
The intended revision window is approximately 11 days; the model can rewind the
whole interval even when unchanged forcing files do not need rebuilding.
