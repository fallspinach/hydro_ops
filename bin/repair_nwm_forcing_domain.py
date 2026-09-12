#!/usr/bin/env python3
"""Atomically enforce the hard NLDAS-2 domain and fill its internal LDASIN gaps."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

import numpy as np
from netCDF4 import Dataset
from scipy.ndimage import distance_transform_edt

from hydro_ops.forcing.coverage import geographic_domain_mask

VARIABLES = ("T2D", "Q2D", "PSFC", "SWDOWN", "LWDOWN", "U2D", "V2D", "RAINRATE")
POLICY = "nldas2_rectangle_nearest_valid_v1"
CONTENT_AUDIT = "all_records_all_8_fields_nldas2_rectangle_and_model_land_v1"
ACTIVE_POLICY = "nldas2_active_gaps_preserve_inactive_v3"
ACTIVE_AUDIT = "all_records_active_complete_outside_masked_inactive_preserved_v3"


def missing_mask(variable, values: np.ndarray) -> np.ndarray:
    masked = np.ma.asarray(values)
    dense = np.asarray(np.ma.getdata(masked))
    missing = np.ma.getmaskarray(masked) | ~np.isfinite(dense)
    if "_FillValue" in variable.ncattrs():
        missing |= dense == variable.getncattr("_FillValue")
    return missing | (dense < -1.0e20) | (dense > 1.0e20)


def load_model_land_mask(path: Path, latitude: np.ndarray, longitude: np.ndarray) -> np.ndarray:
    with Dataset(path) as model:
        land = np.asarray(model["XLAND"][0]) == 1
        model_latitude = np.asarray(model["XLAT"][0])
        model_longitude = np.asarray(model["XLONG"][0])
    if land.shape != latitude.shape:
        raise ValueError(f"model land mask shape {land.shape} differs from forcing {latitude.shape}")
    if not (
        np.allclose(model_latitude, latitude, rtol=0, atol=1.0e-5, equal_nan=True)
        and np.allclose(model_longitude, longitude, rtol=0, atol=1.0e-5, equal_nan=True)
    ):
        raise ValueError("model land-mask coordinates differ from the forcing grid")
    return land


def completeness(
    path: Path, model_mask_path: Path, *, mask_inactive: bool = True,
) -> dict[str, object]:
    """Audit every record over both the hard forcing domain and actual model land."""
    missing_domain = {name: 0 for name in VARIABLES}
    missing_land = {name: 0 for name in VARIABLES}
    valid_inactive = {name: 0 for name in VARIABLES}
    land_hashes = {name: hashlib.sha256() for name in VARIABLES}
    inactive_hashes = {name: hashlib.sha256() for name in VARIABLES}
    with Dataset(path) as data:
        latitude = np.asarray(data["lat"][:])
        longitude = np.asarray(data["lon"][:])
        domain = geographic_domain_mask(latitude, longitude)
        land = load_model_land_mask(model_mask_path, latitude, longitude)
        if np.any(land & ~domain):
            raise ValueError("model land mask extends beyond the hard NLDAS-2 domain")
        for name in VARIABLES:
            variable = data[name]
            for index in range(variable.shape[0]):
                values = variable[index]
                missing = missing_mask(variable, values)
                missing_domain[name] += int(np.count_nonzero(missing & domain))
                missing_land[name] += int(np.count_nonzero(missing & land))
                if mask_inactive:
                    valid_inactive[name] += int(np.count_nonzero(~missing & ~domain))
                    land_hashes[name].update(np.ascontiguousarray(np.ma.getdata(values)[land]).tobytes())
                    inactive_hashes[name].update(np.ascontiguousarray(np.ma.getdata(values)[domain & ~land]).tobytes())
    complete = not any(missing_land.values()) and not any(
        valid_inactive.values() if mask_inactive else missing_domain.values()
    )
    return {
        "complete": complete,
        "missing_domain": missing_domain,
        "missing_model_land": missing_land,
        **({"valid_outside_domain": valid_inactive,
            "inactive_in_domain_sha256": {name: digest.hexdigest() for name, digest in inactive_hashes.items()},
            "active_sha256": {name: digest.hexdigest() for name, digest in land_hashes.items()}}
           if mask_inactive else {}),
    }


def repair(
    source: Path,
    destination: Path,
    *,
    model_mask_path: Path,
    force: bool = False,
    work_directory: Path | None = None,
    mask_inactive: bool = True,
    preserve_active: bool = False,
) -> dict[str, object]:
    if destination.exists() and not force and destination != source:
        raise FileExistsError(f"Output exists; use --force to replace it: {destination}")
    with Dataset(source) as existing:
        if str(getattr(existing, "forcing_domain_policy", "")) == "nldas2_rectangle_nwm_active_land_v2":
            raise ValueError("File was filtered with the retired v2 policy; restore inactive values from source data first")
    policy = ACTIVE_POLICY if mask_inactive else POLICY
    audit_policy = ACTIVE_AUDIT if mask_inactive else CONTENT_AUDIT
    existing_audit = completeness(source, model_mask_path, mask_inactive=mask_inactive)
    if preserve_active and any(existing_audit["missing_model_land"].values()):
        raise ValueError("Mask-only operation requires complete active-land forcing")
    with Dataset(source) as existing:
        policy_current = str(getattr(existing, "forcing_domain_policy", "")) == policy
        audit_current = (
            str(getattr(existing, "forcing_domain_content_audit", "")) == audit_policy
        )
    if destination == source and policy_current and audit_current and existing_audit["complete"] and not force:
        return {
            "path": str(destination),
            "status": "already_repaired_and_validated",
            **existing_audit,
        }
    if (
        destination == source
        and policy_current
        and existing_audit["complete"]
        and not force
    ):
        # The complete content audit has already been performed above. Add its
        # versioned certificate without copying a large, otherwise unchanged file.
        with Dataset(source, "r+") as existing:
            existing.setncattr("forcing_domain_content_audit", audit_policy)
        return {
            "path": str(destination),
            "status": "validated_and_certified",
            **existing_audit,
        }
    destination.parent.mkdir(parents=True, exist_ok=True)
    if work_directory:
        work_directory.mkdir(parents=True, exist_ok=True)
        temporary = work_directory / (destination.name + ".domain-repair.work")
    else:
        temporary = destination.with_name(destination.name + ".domain-repair.part")
    temporary.unlink(missing_ok=True)
    shutil.copy2(source, temporary)
    counts = {name: 0 for name in VARIABLES}
    maxima = {name: 0.0 for name in VARIABLES}
    masked_counts = {name: 0 for name in VARIABLES}
    # Coverage is normally invariant over all 24 records. Cache the relatively
    # expensive nearest-donor lookup while still detecting and handling any
    # genuinely different hourly mask.
    donor_cache: dict[bytes, tuple[np.ndarray, np.ndarray, np.ndarray, float]] = {}
    try:
        with Dataset(temporary, "r+") as data:
            latitude = np.asarray(data["lat"][:])
            longitude = np.asarray(data["lon"][:])
            domain = geographic_domain_mask(latitude, longitude)
            land = load_model_land_mask(model_mask_path, latitude, longitude)
            if np.any(land & ~domain):
                raise ValueError("model land mask extends beyond the hard NLDAS-2 domain")
            for name in VARIABLES:
                variable = data[name]
                if mask_inactive and "_FillValue" not in variable.ncattrs():
                    raise ValueError(f"{name} has no _FillValue for masking inactive cells")
                for index in range(variable.shape[0]):
                    record = variable[index]
                    values = np.asarray(np.ma.getdata(record)).copy()
                    missing = missing_mask(variable, record)
                    targets = missing & (land if mask_inactive else domain)
                    if not np.any(targets):
                        if mask_inactive:
                            count = int(np.count_nonzero(~missing & ~domain))
                            masked_counts[name] += count
                            if not count:
                                continue
                            values[~domain] = variable.getncattr("_FillValue")
                            variable[index] = values
                        continue
                    key = np.packbits(missing, axis=None).tobytes()
                    cached = donor_cache.get(key)
                    if cached is None:
                        donors = ~missing & domain
                        if not np.any(donors):
                            raise ValueError(f"{name}[{index}] has no valid in-domain donor")
                        distance, indices = distance_transform_edt(
                            ~donors, return_indices=True
                        )
                        rows, columns = np.nonzero(targets)
                        donor_linear = np.ravel_multi_index(
                            (indices[0][targets], indices[1][targets]), values.shape
                        )
                        cached = (
                            rows,
                            columns,
                            donor_linear,
                            float(distance[targets].max()),
                        )
                        donor_cache[key] = cached
                    rows, columns, donor_linear, maximum = cached
                    values[rows, columns] = values.ravel()[donor_linear]
                    if mask_inactive:
                        masked_counts[name] += int(np.count_nonzero(~missing & ~domain))
                        values[~domain] = variable.getncattr("_FillValue")
                    variable[index] = values
                    counts[name] += len(rows)
                    maxima[name] = max(maxima[name], maximum)
            data.setncattr("forcing_domain_policy", policy)
            data.setncattr("forcing_domain_content_audit", audit_policy)
            if mask_inactive:
                data.setncattr("forcing_active_mask", str(model_mask_path.resolve()))
                data.setncattr("forcing_active_mask_rule", "fill missing XLAND == 1 inside NLDAS-2; preserve other in-domain values; mask outside bounds")
                data.setncattr("forcing_outside_domain_masked_counts", json.dumps(masked_counts, sort_keys=True))
            data.setncattr("forcing_domain_bounds", "25<=lat<=53,-125<=lon<=-67")
            data.setncattr("forcing_domain_boundary", "hard; no target or donor outside bounds")
            data.setncattr("forcing_gap_fill_method", "nearest valid grid cell within bounds")
            data.setncattr("forcing_gap_fill_counts", json.dumps(counts, sort_keys=True))
            data.setncattr("forcing_gap_fill_max_distance_cells", json.dumps(maxima, sort_keys=True))
        final_audit = completeness(temporary, model_mask_path, mask_inactive=mask_inactive)
        if not final_audit["complete"]:
            raise ValueError(f"repair left missing forcing values: {final_audit}")
        if mask_inactive:
            for name in VARIABLES:
                if existing_audit["inactive_in_domain_sha256"][name] != final_audit["inactive_in_domain_sha256"][name]:
                    raise ValueError(f"Repair changed inactive in-domain values in {name}")
                if (existing_audit["missing_model_land"][name] == 0
                        and existing_audit["active_sha256"][name] != final_audit["active_sha256"][name]):
                    raise ValueError(f"Inactive masking changed existing active values in {name}")
        if temporary.parent == destination.parent:
            temporary.replace(destination)
        else:
            publishing = destination.with_name(destination.name + ".domain-repair.part")
            publishing.unlink(missing_ok=True)
            shutil.copy2(temporary, publishing)
            os.replace(publishing, destination)
            temporary.unlink()
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return {
        "path": str(destination),
        "status": "repaired",
        "counts": counts,
        "maximum_distance_cells": maxima,
        **({"masked_outside_domain_counts": masked_counts} if mask_inactive else {}),
        **final_audit,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("forcing", nargs="+", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--in-place", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--work-directory", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--model-mask",
        type=Path,
        default=Path(
            "nwm/static/operational/nwm.v3.1.6/domain/wrfinput_CONUS_NLDAS2.nc"
        ),
    )
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--active-gaps-only", "--mask-inactive", dest="mask_inactive", action="store_true", default=True,
                        help="fill active gaps, preserve inactive values inside NLDAS-2, mask outside (default); --mask-inactive is a deprecated alias")
    parser.add_argument("--preserve-active", action="store_true",
                        help="reject missing active values rather than filling them")
    args = parser.parse_args()
    if not args.audit_only and args.in_place == bool(args.output_dir):
        parser.error("choose exactly one of --in-place or --output-dir")
    reports = []
    for source in args.forcing:
        if args.audit_only:
            report = {
                "path": str(source),
                "status": "audited",
                **completeness(source, args.model_mask, mask_inactive=args.mask_inactive),
            }
            reports.append(report)
            print(json.dumps(report, sort_keys=True), flush=True)
            continue
        destination = source if args.in_place else args.output_dir / source.name
        report = repair(
            source,
            destination,
            model_mask_path=args.model_mask,
            force=args.force,
            work_directory=args.work_directory,
            mask_inactive=args.mask_inactive,
            preserve_active=args.preserve_active,
        )
        reports.append(report)
        print(json.dumps(report, sort_keys=True), flush=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        partial = args.report.with_suffix(args.report.suffix + ".part")
        partial.write_text(json.dumps(reports, indent=2, sort_keys=True) + "\n")
        partial.replace(args.report)
    return 0 if all(report["complete"] for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
