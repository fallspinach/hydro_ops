"""Create and fully audit separate static-mask pilot copies; never edit source files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.forcing.coverage import geographic_domain_mask

FIELDS = ("T2D", "Q2D", "PSFC", "LWDOWN", "SWDOWN", "U2D", "V2D", "RAINRATE")
POLICY = "nldas2_seven_meteorological_union_clip_pilot_v1"


def digest(values):
    return hashlib.sha256(np.ascontiguousarray(values).tobytes()).hexdigest()


def missing(variable, values):
    result = ~np.isfinite(values) | (values < -1e20) | (values > 1e20)
    for key in ("_FillValue", "missing_value"):
        if key in variable.ncattrs():
            result |= np.isin(values, variable.getncattr(key))
    return result


def build_mask(study, model, output):
    """Freeze the studied candidate with coordinates and active-grid identity."""
    if output.exists():
        raise FileExistsError(output)
    with np.load(study / "target_masks.npz") as samples, Dataset(model) as grid:
        keep, active = samples["candidate__seven_meteorological"], samples["land"]
        lat, lon = np.asarray(grid["XLAT"][0]), np.asarray(grid["XLONG"][0])
        domain = geographic_domain_mask(lat, lon)
        np.testing.assert_array_equal(active, np.asarray(grid["XLAND"][0]) == 1)
        if np.any(active & ~keep) or np.any(keep & ~domain):
            raise ValueError("Candidate violates active coverage or rectangle boundary")
        output.parent.mkdir(parents=True, exist_ok=True)
        with Dataset(output, "w") as dst:
            for name, size in zip(("y", "x"), keep.shape, strict=True):
                dst.createDimension(name, size)
            for name, values in (("lat", lat), ("lon", lon), ("keep", keep), ("active", active)):
                var = dst.createVariable(name, "u1" if values.dtype == bool else values.dtype,
                                         ("y", "x"), zlib=True, complevel=2, shuffle=True)
                var[:] = values
            dst.setncatts({"policy": POLICY, "status": "pilot_only",
                          "keep_sha256": digest(keep), "active_sha256": digest(active),
                          "derivation": "NLDAS2 rectangle AND (active OR seven meteorological sampled union)",
                          "source_study": str(study.resolve()), "model_mask": str(model.resolve()),
                          "study_summary_sha256": hashlib.sha256((study / "target_summary.json").read_bytes()).hexdigest()})


def run(source, mask_path, output, work):
    started = time.monotonic()
    if source.resolve() == output.resolve() or output.exists():
        raise ValueError("Pilot destination must be a new, separate file")
    source_stat = source.stat()
    with Dataset(mask_path) as mask:
        keep, active = np.asarray(mask["keep"][:], bool), np.asarray(mask["active"][:], bool)
        lat, lon = np.asarray(mask["lat"][:]), np.asarray(mask["lon"][:])
        if digest(keep) != mask.keep_sha256 or digest(active) != mask.active_sha256:
            raise ValueError("Mask checksum mismatch")
        if np.any(active & ~keep) or np.any(keep & ~geographic_domain_mask(lat, lon)):
            raise ValueError("Unsafe static mask")
    report = {"source": str(source.resolve()), "output": str(output.resolve()),
              "mask": str(mask_path.resolve()), "mask_sha256": digest(keep),
              "status": "testing", "fields": {}, "auxiliary_verified": [],
              "active_cells": int(active.sum()), "keep_cells": int(keep.sum())}
    work.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="static-mask-pilot-", dir=work) as temporary:
        candidate = Path(temporary) / output.name
        expected = {}
        with Dataset(source) as src, Dataset(candidate, "w", format="NETCDF4") as dst:
            src.set_auto_maskandscale(False)
            if getattr(src, "forcing_source", "") != "nldas2":
                raise ValueError("Pilot supports NLDAS-2-based data only")
            for name, coords in (("lat", lat), ("lon", lon)):
                np.testing.assert_allclose(src[name][:], coords, rtol=0, atol=1e-5)
            if not set(FIELDS).issubset(src.variables):
                raise ValueError("Missing required forcing variable")
            dst.setncatts({k: src.getncattr(k) for k in src.ncattrs()})
            dst.setncatts({"forcing_domain_previous_policy": str(getattr(src, "forcing_domain_policy", "none")),
                          "forcing_domain_policy": POLICY,
                          "forcing_domain_content_audit": "pilot_pending_validation",
                          "forcing_static_mask": str(mask_path.resolve()),
                          "forcing_static_mask_sha256": digest(keep),
                          "forcing_static_mask_status": "pilot_only_not_operational_publication"})
            for name, dim in src.dimensions.items():
                dst.createDimension(name, None if dim.isunlimited() else len(dim))
            for name, var in src.variables.items():
                options = {}
                if "_FillValue" in var.ncattrs():
                    options["fill_value"] = var._FillValue
                filters = var.filters() or {}
                if filters.get("zlib"):
                    options.update(zlib=True, complevel=filters["complevel"], shuffle=filters["shuffle"])
                chunks = var.chunking()
                if isinstance(chunks, list):
                    options["chunksizes"] = chunks
                out = dst.createVariable(name, var.dtype, var.dimensions, **options)
                out.setncatts({k: var.getncattr(k) for k in var.ncattrs() if k != "_FillValue"})
                out.set_auto_maskandscale(False)
                indices = range(var.shape[0]) if var.dimensions and var.dimensions[0] == "time" else [Ellipsis]
                hashes = {k: hashlib.sha256() for k in ("all", "active", "retained")}
                stats = {"removed_valid_cell_hours": 0, "missing_active_cell_hours": 0,
                         "missing_retained_cell_hours": 0, "records": 0}
                for index in indices:
                    values = np.asarray(var[index])
                    if name in FIELDS:
                        absent = missing(var, values)
                        if np.any(absent & active):
                            raise ValueError(f"Source has missing active data: {name} record {index}")
                        if "_FillValue" not in var.ncattrs():
                            raise ValueError(f"No fill value: {name}")
                        hashes["active"].update(np.ascontiguousarray(values[active]).tobytes())
                        hashes["retained"].update(np.ascontiguousarray(values[keep]).tobytes())
                        stats["removed_valid_cell_hours"] += int((~absent & ~keep).sum())
                        stats["missing_retained_cell_hours"] += int((absent & keep).sum())
                        stats["records"] += 1
                        values = values.copy()
                        values[~keep] = var._FillValue
                    hashes["all"].update(np.ascontiguousarray(values).tobytes())
                    out[index] = values
                expected[name] = {k: v.hexdigest() for k, v in hashes.items()}
                if name in FIELDS:
                    report["fields"][name] = stats
                print(json.dumps({"source": source.name, "written": name}), flush=True)
        write_seconds = time.monotonic() - started
        with Dataset(candidate, "r+") as check:
            check.set_auto_maskandscale(False)
            for name, var in check.variables.items():
                hashes = {k: hashlib.sha256() for k in ("all", "active", "retained")}
                indices = range(var.shape[0]) if var.dimensions and var.dimensions[0] == "time" else [Ellipsis]
                for index in indices:
                    values = np.asarray(var[index])
                    hashes["all"].update(np.ascontiguousarray(values).tobytes())
                    if name in FIELDS:
                        absent = missing(var, values)
                        if np.any(absent & active) or np.any(~absent & ~keep):
                            raise ValueError(f"Output coverage failed: {name} record {index}")
                        hashes["active"].update(np.ascontiguousarray(values[active]).tobytes())
                        hashes["retained"].update(np.ascontiguousarray(values[keep]).tobytes())
                actual = {k: v.hexdigest() for k, v in hashes.items()}
                if actual != expected[name]:
                    raise ValueError(f"Read-back values differ: {name}")
                if name in FIELDS:
                    report["fields"][name].update(active_unchanged=True, retained_unchanged=True,
                                                  valid_outside_mask=0, active_sha256=actual["active"])
                else:
                    report["auxiliary_verified"].append(name)
            check.forcing_domain_content_audit = "all_records_active_and_retained_identical_outside_static_mask_missing_pilot_v1"
        current = source.stat()
        if (source_stat.st_ino, source_stat.st_size, source_stat.st_mtime_ns) != (current.st_ino, current.st_size, current.st_mtime_ns):
            raise ValueError("Source changed during pilot; refuse publication")
        part = output.with_name(output.name + ".part")
        if part.exists():
            raise FileExistsError(part)
        shutil.copyfile(candidate, part)
        with candidate.open("rb") as left, part.open("rb") as right:
            if hashlib.file_digest(left, "sha256").hexdigest() != hashlib.file_digest(right, "sha256").hexdigest():
                raise ValueError("Published-copy checksum mismatch")
        os.replace(part, output)
    report.update(status="passed", source_bytes=source_stat.st_size, output_bytes=output.stat().st_size,
                  write_seconds=write_seconds, total_seconds=time.monotonic() - started,
                  source_unchanged=True, note="Separate pilot audit, not a replacement production manifest; ancillary source/QC fields retained unchanged.")
    output.with_name(output.name + ".pilot-audit.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mask", type=Path, required=True)
    parser.add_argument("--study", type=Path)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--work", type=Path)
    args = parser.parse_args()
    if args.study:
        build_mask(args.study, args.model, args.mask)
    else:
        if not all((args.source, args.output, args.work)):
            parser.error("--source, --output and --work are required for a pilot")
        run(args.source, args.mask, args.output, args.work)


if __name__ == "__main__":
    main()
