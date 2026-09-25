"""Opt-in daily NRT writer: conservative GFS gap fallback and explicit provenance."""

import hashlib
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, date2num, num2date
from scipy.ndimage import distance_transform_edt

from hydro_ops.download.gfs import GfsDownloader
from hydro_ops.forcing.gfs_conservative import ConservativeGap
from hydro_ops.forcing.gfs_gap import MET_FIELDS, remap_hour, sha
from hydro_ops.forcing.source_provenance import classify_hour

GFS_MET_SOURCE_ID = 4
GFS_PRECIP_SOURCE_ID = 8
POLICY = "nrt_hrrr_gfs_static_envelope_v1"


def _atomic_json(path, payload):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=path.name + ".", delete=False) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    os.replace(temporary, path)


def precipitation_supported(ids, values, qc):
    """Preserve explicitly selected observational/analysis precipitation, not old fills."""
    return (np.isin(ids, [1, 2, 3, 4, 5, 7]) & np.isfinite(values) & (values >= 0)
            & ((qc.astype(np.uint16) & 8) == 0))


def fill_active_holes(values, missing, active, keep, gap, cache):
    targets = missing & active & ~gap
    if not targets.any():
        return values, targets, 0.0
    donors = ~missing & keep & ~gap
    if not donors.any():
        raise ValueError("No in-domain primary donors for active coastal repair")
    key = (sha(np.packbits(donors)), sha(np.packbits(targets)))
    if key not in cache:
        distances, addresses = distance_transform_edt(~donors, return_indices=True)
        target_indices = np.flatnonzero(targets)
        donor_indices = np.ravel_multi_index((addresses[0][targets], addresses[1][targets]), values.shape)
        cache[key] = (target_indices, donor_indices, float(distances[targets].max()))
    target_indices, donor_indices, maximum = cache[key]
    values.ravel()[target_indices] = values.ravel()[donor_indices]
    return values, targets, maximum


def write_changed_chunks(var, index, original, updated):
    """Avoid recompressing untouched chunks; retain the caller's full-field audit."""
    chunks = var.chunking()
    if not isinstance(chunks, (list, tuple)) or len(chunks) != 3 or chunks[0] != 1:
        var[index] = updated
        return
    # Bitwise comparison also preserves signed zero and NaN payload changes.
    unsigned = np.dtype(f'u{updated.dtype.itemsize}')
    changed = np.asarray(original).view(unsigned) != updated.view(unsigned)
    cy, cx = chunks[1:]
    for y in range(0, updated.shape[0], cy):
        for x in range(0, updated.shape[1], cx):
            region = (slice(y, y + cy), slice(x, x + cx))
            if changed[region].any():
                var[index, region[0], region[1]] = updated[region]


def publish_gfs_day(source_path, output_path, envelope_path, geometry_path, conservative_path,
                    cache_path, work, *, nldas_available, as_of=None, historical_test=False,
                    allow_mixed=False, require_native_repair=False, sparse_writes=False,
                    expected_hours=24):
    if source_path.resolve() == output_path.resolve() or output_path.exists():
        raise ValueError("Opt-in writer requires a new, separate destination")
    if not historical_test and as_of is None:
        raise ValueError("Operational fallback requires an explicit as-of cutoff")
    if as_of is not None and as_of.tzinfo is None:
        raise ValueError("as-of cutoff must be timezone-aware")
    if nldas_available:
        return {"status": "rebuild_with_nldas2", "reason": "NLDAS-2 takes precedence; no GFS file written"}
    # Historical replay may bypass current archive availability, never cell provenance.
    with Dataset(source_path) as source:
        modes = [classify_hour(source["forcing_source_id"][i]) for i in range(len(source.dimensions["time"]))]
    if any(mode not in ({"nldas2", "hrrr"} if allow_mixed else {"hrrr"}) for mode in modes):
        return {"status": "rebuild_with_nldas2", "reason": "Non-HRRR hourly/cell provenance protected; route through native-source repair",
                "hourly_sources": modes}
    with Dataset(envelope_path) as envelope:
        keep, active = np.asarray(envelope["keep"][:], bool), np.asarray(envelope["active"][:], bool)
        lat, lon = np.asarray(envelope["lat"][:]), np.asarray(envelope["lon"][:])
    with np.load(geometry_path) as data:
        geometry = {name: data[name] for name in data.files}
    if (sha(keep) != str(geometry["envelope_sha256"]) or tuple(keep.shape) != tuple(geometry["shape"])
            or sha(lat) != str(geometry["target_lat_sha256"]) or sha(lon) != str(geometry["target_lon_sha256"])):
        raise ValueError("Envelope/geometry grid identity mismatch")
    gap = np.zeros(keep.shape, dtype=bool)
    indices = geometry["indices"]
    gap.ravel()[indices] = True
    if np.any(gap & ~keep) or np.any(active & ~keep):
        raise ValueError("Unsafe fallback envelope")
    conservative = ConservativeGap(conservative_path, geometry)
    downloader = GfsDownloader(cache_path, work)
    original_stat = source_path.stat()
    with Dataset(source_path) as original:
        np.testing.assert_allclose(original["lat"][:], lat, rtol=0, atol=1e-5)
        np.testing.assert_allclose(original["lon"][:], lon, rtol=0, atol=1e-5)
        times = num2date(original["time"][:], original["time"].units, only_use_cftime_datetimes=False)
        if (not 1 <= expected_hours <= 24 or len(times) != expected_hours
                or [t.hour for t in times] != list(range(expected_hours))
                or len({t.date() for t in times}) != 1
                or any(t.minute or t.second or t.microsecond for t in times)):
            raise ValueError("Expected contiguous UTC hours beginning at 00")
    original_manifest = source_path.with_name(source_path.name + ".manifest.json")
    provenance = json.loads(original_manifest.read_text()) if original_manifest.exists() else None
    work.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    audit = {"input": str(source_path.resolve()), "output": str(output_path.resolve()), "status": "pending",
             "policy": POLICY, "historical_test": historical_test, "hours": [],
             "gfs_cells": len(indices), "active_gfs_cells": int((active & gap).sum()),
             "precipitation_remapping": "CDO conservative destarea", "source_unchanged": False}
    expected = {}
    donor_cache = {}
    with tempfile.TemporaryDirectory(prefix="gfs-nrt-day-", dir=work) as temporary:
        staged = Path(temporary) / output_path.name
        shutil.copyfile(source_path, staged)
        with Dataset(staged, "r+") as dst:
            flags = dst.variables.get("gfs_fallback_qc") or dst.createVariable("gfs_fallback_qc", "u1", ("time", "y", "x"),
                                       zlib=True, complevel=2, shuffle=True, chunksizes=(1, min(256, keep.shape[0]), min(256, keep.shape[1])))
            flags.flag_masks = np.array([1, 2, 4, 8, 16, 32], dtype=np.uint8)
            flags.flag_meanings = "gfs_meteorology gfs_precipitation model_HGT_used relative_humidity_clipped source_hour_roundoff_clipped active_hole_repaired"
            # Explicit CF missing metadata is essential: netCDF4 masks its implicit
            # default fill, but xarray otherwise tries to decode it as a huge date.
            cycle = dst.variables.get("gfs_forecast_reference_time") or dst.createVariable("gfs_forecast_reference_time", "f8", ("time",),
                                       fill_value=np.nan)
            cycle.units = dst["time"].units
            cycle.calendar = getattr(dst["time"], "calendar", "standard")
            leads = dst.variables.get("gfs_forecast_lead_hours") or dst.createVariable("gfs_forecast_lead_hours", "i2", ("time",))
            leads.units = "hours"
            dst["forcing_source_id"].flag_values = np.array([0, 1, 2, 3, 4], dtype=np.uint8)
            dst["forcing_source_id"].flag_meanings = "missing nldas2 hrrr nldas2_hrrr_hybrid gfs_short_forecast"
            dst["precip_source_id"].flag_values = np.arange(9, dtype=np.uint8)
            dst["precip_source_id"].flag_meanings = "missing mrms_pass2 mrms_pass1 stage4_archive stage4_realtime nldas2 hrrr stage4_06h_constrained gfs_short_forecast"
            for index, stamp in enumerate(times):
                valid = stamp.replace(tzinfo=UTC)
                use_gfs = modes[index] == "hrrr"
                if use_gfs:
                    gfs = downloader.hour(valid, as_of=as_of)
                    fallback, rh_clipped = remap_hour(gfs, geometry, precipitation_remapper=conservative, return_quality=True)
                    cycle[index] = date2num(datetime.fromisoformat(gfs.attrs["cycle"]), cycle.units)
                    leads[index] = gfs.attrs["lead"]
                    attributes = gfs.attrs
                else:
                    fallback = {}
                    cycle[index] = np.ma.masked
                    leads[index] = 0
                    attributes = {"cycle": None, "lead": 0, "url": None,
                                  "retrieved_utc": None, "remote_last_modified": ""}
                usage = np.zeros(keep.shape, dtype=np.uint8)
                if use_gfs:
                    usage[gap] = 1
                    usage.ravel()[indices[geometry["terrain_fallback"]]] |= 4
                    usage.ravel()[indices[rh_clipped]] |= 8
                    if any(json.loads(gfs.attrs["negative_roundoff_clipped"]).values()):
                        usage[gap] |= 16
                rain_ids = np.asarray(dst["precip_source_id"][index])
                rain_qc = np.asarray(dst["precip_qc_flags"][index], dtype=np.uint16)
                rain_values = np.ma.filled(dst["RAINRATE"][index], np.nan)
                supported = precipitation_supported(rain_ids, rain_values, rain_qc)
                rain_targets = gap & ~supported & use_gfs
                usage[rain_targets] |= 2
                report = {"valid_time": valid.isoformat(), "primary_source": modes[index],
                          "cycle": attributes["cycle"], "lead": int(attributes["lead"]),
                          "as_of": as_of.isoformat() if as_of else None, "source_url": attributes["url"],
                          "retrieved_utc": attributes["retrieved_utc"], "remote_last_modified": attributes.get("remote_last_modified", ""),
                          "gfs_rain_cells": int(rain_targets.sum()), "preserved_precipitation_cells_in_gap": int((gap & supported).sum()),
                          "active_repairs": {}, "repair_max_distance_cells": {}}
                for name in (*MET_FIELDS, "RAINRATE"):
                    var = dst[name]
                    stored = var[index]
                    values = np.asarray(np.ma.getdata(stored)).copy()
                    missing = np.ma.getmaskarray(stored) | ~np.isfinite(values)
                    targets = (gap & use_gfs) if name != "RAINRATE" else rain_targets
                    unchanged = keep & ~targets & ~missing
                    before_hash = sha(values[unchanged])
                    sparse_targets = np.ones(len(indices), bool) if name != "RAINRATE" else rain_targets.ravel()[indices]
                    if use_gfs:
                        values.ravel()[indices[sparse_targets]] = fallback[name][sparse_targets]
                    if require_native_repair and np.any(missing & active & ~gap):
                        raise ValueError("Native primary repair incomplete; refusing unbounded target-grid filling")
                    values, repaired, maximum = fill_active_holes(values, missing, active, keep, gap, donor_cache)
                    usage[repaired] |= 32
                    report["active_repairs"][name] = int(repaired.sum())
                    report["repair_max_distance_cells"][name] = maximum
                    values[~keep] = var._FillValue
                    if sha(values[unchanged]) != before_hash:
                        raise ValueError("Unrelated valid source values changed")
                    expected[index, name] = sha(values)
                    if sparse_writes:
                        write_changed_chunks(var, index, np.ma.getdata(stored), values)
                    else:
                        var[index] = values
                flags[index] = usage
                met_ids = np.asarray(dst["forcing_source_id"][index])
                if use_gfs:
                    met_ids[gap] = GFS_MET_SOURCE_ID
                met_ids[~keep] = 0
                dst["forcing_source_id"][index] = met_ids
                met_qc = np.asarray(dst["forcing_qc_flags"][index], dtype=np.uint32)
                if use_gfs:
                    met_qc[gap] = 0  # Old HRRR missing-data flags do not describe the new GFS bundle.
                dst["forcing_qc_flags"][index] = met_qc
                rain_ids[rain_targets], rain_ids[~keep] = GFS_PRECIP_SOURCE_ID, 0
                dst["precip_source_id"][index] = rain_ids
                rain_qc[rain_targets] = 4  # FALLBACK_USED; no carryover of unrelated source flags.
                dst["precip_qc_flags"][index] = rain_qc
                confidence = np.asarray(dst["precip_confidence"][index])
                confidence[rain_targets] = 0.15
                dst["precip_confidence"][index] = confidence
                audit["hours"].append(report)
                print(json.dumps(report), flush=True)
            source_label = "nldas2" if set(modes) == {"nldas2"} else "hrrr_gfs_nrt" if set(modes) == {"hrrr"} else "mixed_nldas2_hrrr_gfs_nrt"
            dst.setncatts({"forcing_source": source_label, "forcing_domain_policy": POLICY,
                "forcing_static_mask_sha256": sha(keep), "gfs_conservative_weights": str(conservative_path.resolve()),
                "gfs_publication_status": "historical_test" if historical_test else "opt_in_nrt",
                "gfs_precipitation_confidence": "0.15 is a provisional uncalibrated heuristic",
                "gfs_repair_policy": "fill missing active primary cells inside envelope, using non-GFS primary donors; then clip envelope",
                "gfs_parent_policy": "Existing non-gap precipitation/CNRFC processing is inherited, not revalidated by this gap test"})
            if "forcing_source_id" in dst.ncattrs():
                dst.delncattr("forcing_source_id")
        with Dataset(staged, "r+") as checked:
            for index in range(expected_hours):
                for name in (*MET_FIELDS, "RAINRATE"):
                    stored = checked[name][index]
                    values = np.ma.getdata(stored)
                    missing = np.ma.getmaskarray(stored) | ~np.isfinite(values)
                    if sha(values) != expected[index, name] or np.any(missing & active) or np.any(~missing & ~keep):
                        raise ValueError(f"Full daily-file audit failed: {index} {name}")
            checked.forcing_domain_content_audit = f"all_{expected_hours}_hours_all_8_fields_active_complete_outside_envelope_missing_gfs_v1"
        stat = source_path.stat()
        if (stat.st_ino, stat.st_size, stat.st_mtime_ns) != (original_stat.st_ino, original_stat.st_size, original_stat.st_mtime_ns):
            raise ValueError("Input changed during processing")
        partial = output_path.with_name(output_path.name + ".part")
        shutil.copyfile(staged, partial)
        with staged.open("rb") as left, partial.open("rb") as right:
            checksum = hashlib.file_digest(left, "sha256").hexdigest()
            if checksum != hashlib.file_digest(right, "sha256").hexdigest():
                raise ValueError("Publication transfer checksum differs")
        os.replace(partial, output_path)
    audit.update(status="passed", source_unchanged=True, published_sha256=checksum,
                 gfs_hours=sum(mode == "hrrr" for mode in modes), hourly_primary_sources=modes,
                 all_original_valid_unselected_values_inside_envelope_unchanged=True, missing_active_values=0,
                 valid_outside_envelope=0)
    _atomic_json(output_path.with_name(output_path.name + ".gfs-audit.json"), audit)
    manifest = {"day": times[0].date().isoformat(), "daily_file": str(output_path.resolve()), "verified": True,
                "verification": "all_records_all_fields_gfs_nrt_v1", "parent_manifest": provenance,
                "gfs_audit": str(output_path.with_name(output_path.name + ".gfs-audit.json").resolve()),
                "historical_test": historical_test, "forcing_stream": "nrt"}
    manifest["source_files"] = [{"path": str(source_path.resolve()), "time_index": index,
        "bytes": original_stat.st_size, "mtime": original_stat.st_mtime,
        "gfs_cycle": hour["cycle"], "gfs_lead": hour["lead"], "gfs_url": hour["source_url"]}
        for index, hour in enumerate(audit["hours"])]
    _atomic_json(output_path.with_name(output_path.name + ".manifest.json"), manifest)
    return audit
