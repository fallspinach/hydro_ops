# NLDAS-2 Earthdata Cloud migration

## Deployment state

On September 24, 2026 UTC, after acceptance job **4629232** and user approval,
the operational default was switched to `discovery = "cmr"` in
`config/project.toml`. The settings loader also defaults to CMR when older site
configuration omits that key. Daily/six-hourly refreshes and standalone downloads
now use Earthdata Cloud unless explicitly overridden. NASA's legacy HTTPS
shutdown deadline is September 30, 2026, with earlier phased shutdowns possible.

Post-cutover verification without any backend override resolved `cmr` and found
13 cloud granules for September 20. Incremental operational launcher **4629262**
uses the normal cron wrapper and `--cycle six-hourly`. It is dependency-ordered
after active NLDAS-arrival worker **4629221** to avoid concurrent input/output
writes. Its log is `forcing/logs/earthdata-cutover-cycle-4629262.out`;
downstream refresh/controller IDs will be recorded there by the normal cycle.
The configuration switch is complete; the post-cutover cycle is not yet claimed
complete. The earlier compute-node cloud acceptance remains valid.

References:

- [NASA migration guidance](https://github.com/nasa/gesdisc-tutorials/blob/main/user-guidance/GES_DISC_Migration_to_Earthdata_Website_and_Earthdata_Cloud.md#changes-to-https-data-access)
- [CMR search API](https://cmr.earthdata.nasa.gov/search/site/docs/search/api.html)

## Discovery and authentication

The `cmr` backend queries `https://cmr.earthdata.nasa.gov/search/granules.json`
for collection **C2033151148-GES_DISC**, NLDAS_FORA0125_H **2.0**, by UTC day.
It handles pagination and checks response counts, collection, filename, exact
timestamp, unique granules, and the cloud download host. It uses the actual HTTPS
data link returned by CMR, not a constructed URL or an HTML directory listing.
Inherited collection links, S3 URLs, and OPeNDAP/service links are not downloads.
No legacy fallback silently masks a cloud failure.

Downloads use existing Earthdata `.netrc` credentials and session cookies.
No AWS account or temporary S3 credentials are needed on this cluster.
Cloud HEAD requests must follow redirects before comparing size/Last-Modified:
the initial live test exposed unnecessary repeat downloads without this option.
GET responses still undergo NetCDF signature validation and atomic publication.

`--discover-latest` retains the conservative lag as the historical repair boundary
but probes newer dates through today. A successful CMR query with zero granules
can mean not-yet-indexed data in that recent range. CMR HTTP/authentication errors
are never treated as an empty day. Historical empty queries remain failures.

## Compatibility and acceptance

`bin/validate_nldas_cloud.py` requires a fresh isolated directory. It downloads
cloud files without changing production sources and records an `acceptance.json`.
It compares all variables and masks, dimensions/dtypes, critical attributes,
grid fingerprints, timestamps, and production-normalized fields; records byte
identity when original hourly files remain; tests per-hour source selection,
verified daily aggregation and daily-file reading, and repeat-download skipping.
Original comparison files are read-only and their identities checked afterward.

The first comparison found **37/37 recent hourly files byte-identical**:
all 24 hours of September 19, 2026, and the 13 available hours of September 20.
Five historical noon samples (1979-01-02, 1981-07-01, 2003-07-01, 2020-07-01,
2026-03-01) matched all values/masks in existing daily collections. Byte equality
is not claimed against those aggregated files; their archive metadata differs.

The extended first attempt stopped at the repeat-download check, identifying
the HEAD-redirect issue. The fixed compute-node acceptance job **4629232 passed
in 1m10s**;
receipt: `forcing/work/nldas-cloud-acceptance-job-4629232/acceptance.json`.
All **42 samples** passed production-normalized reader comparisons. The complete
day passed aggregation and daily-reader checks, all seven tested dates skipped
unchanged repeat downloads, and the current-day CMR query returned zero granules
without an error. Historical variable-attribute differences were limited to daily
archive extrema (`vmin`/`vmax`) and time coverage attributes; no critical units,
fill/scale metadata, grid coordinates, decoded values or masks differed.
The full repository suite passed **404 tests** after these changes; targeted
lint and whitespace checks also passed.

Existing local paths, variable names/units, partial-hour eligibility and daily
archive names are unchanged. No forcing archive needs bulk redownloading or
reprocessing solely because its server changed. Reader/array equivalence is
tested here; this is not a new full-CONUS WRF-Hydro simulation or timing benchmark.

## Testing and cutover configuration

Isolated compatibility test:

```bash
python bin/validate_nldas_cloud.py --work forcing/work/nldas-cloud-new-test \
  --days 2026-09-19 2026-09-20 \
  --sample-days 1979-01-02 1981-07-01 2003-07-01 2020-07-01 2026-03-01
```

Explicit opt-in acquisition (writes the configured input archive):

```bash
python -m hydro_ops.cli submit nldas2 --discovery cmr --discover-latest
```

The accepted default is `[nldas2] discovery = "cmr"` (the equivalent site override
is `HYDRO_OPS_NLDAS_DISCOVERY=cmr`). Both coordinated cron cycles and standalone
downloads use cloud discovery. The legacy `base_url` is ignored by CMR;
changing only that URL is not a migration. Verify cron/SLURM authentication,
latest-hour coverage, and one incremental NRT cycle after cutover. A temporary
`--discovery legacy` rollback is available only while NASA's old service exists.
