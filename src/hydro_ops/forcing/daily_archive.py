"""Atomic daily NetCDF collections for completed hourly forcing products."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np
from netCDF4 import Dataset, num2date

from hydro_ops.forcing.baseline_schema import (
    FIELDS,
    SPECS,
    VERSION,
    CanonicalDiagnostic,
    UnknownDiagnostic,
    canonical_names,
)


def _record_policy_attributes(paths, indices) -> dict[str, str]:
    """Carry CNRFC policy across mixed pre/post-policy windows, never invent it.

    Global attributes from the first record alone are insufficient at July 1,
    2020. Every selected post-boundary record must come from a marked source.
    The attribute describes only records on/after the explicit effective time.
    """
    selected = {}
    for path, index in zip(paths, indices, strict=True):
        selected.setdefault(path, []).append(index)
    policies = []
    missing = []
    for path, records in selected.items():
        with Dataset(path) as source:
            policy = str(getattr(source, "cnrfc_stage4_policy", ""))
            time = source.variables.get("time")
            if time is None or not hasattr(time, "units"):
                continue
            stamps = num2date(time[records], time.units,
                              calendar=getattr(time, "calendar", "standard"))
            applicable = any((t.year, t.month, t.day) >= (2020, 7, 1) for t in stamps)
            if applicable:
                if policy:
                    policies.append(policy)
                else:
                    missing.append(str(path))
    if policies and missing:
        raise ValueError(f"Missing CNRFC policy on selected post-boundary records: {missing}")
    if not policies:
        return {}
    return {
        "cnrfc_stage4_policy": "; ".join(dict.fromkeys(policies)),
        "cnrfc_stage4_policy_effective_from": "2020-07-01T00:00:00Z",
    }


def _attributes(variable) -> dict[str, Any]:
    return {name: variable.getncattr(name) for name in variable.ncattrs() if name != "_FillValue"}


def _chunks(variable, dimensions: dict[str, int]) -> tuple[int, ...] | None:
    if variable.ndim < 2:
        return None
    return tuple(
        1 if name == "time" else min(dimensions[name], 256) for name in variable.dimensions
    )


def _digest(values) -> str:
    array = np.ma.asarray(values)
    mask = np.ma.getmaskarray(array)
    canonical = np.where(mask, 0, array.data)
    digest = hashlib.sha256(np.ascontiguousarray(canonical).tobytes())
    digest.update(np.ascontiguousarray(mask).tobytes())
    return digest.hexdigest()


def _validate_inputs(
    paths: list[Path], expected_hours: int, source_time_indices: list[int],
    normalize_precipitation_timing: bool = False,
) -> tuple[dict[str, int], list[str]]:
    if len(paths) != expected_hours:
        raise ValueError(f"Expected {expected_hours} hourly files, found {len(paths)}")
    dimensions: dict[str, int] | None = None
    variables: list[str] | None = None
    previous_time: float | None = None
    time_units: str | None = None
    signatures = {}
    metadata = {}
    for path, source_index in zip(paths, source_time_indices, strict=True):
        with Dataset(path) as dataset:
            current_dimensions = {
                name: len(value) for name, value in dataset.dimensions.items() if name != "time"
            }
            current_variables = sorted(dataset.variables)
            current_variables = sorted(set(current_variables) | canonical_names(dataset))
            if normalize_precipitation_timing and "precip_source_id" in current_variables:
                current_variables = sorted(set(current_variables) | {"precip_timing_source_id"})
            if dimensions is None:
                dimensions, variables = current_dimensions, current_variables
            elif dimensions != current_dimensions or variables != current_variables:
                raise ValueError(
                    f"Hourly NetCDF schema differs: {path}; "
                    f"missing_variables={sorted(set(variables) - set(current_variables))}; "
                    f"extra_variables={sorted(set(current_variables) - set(variables))}; "
                    f"dimensions={current_dimensions}; expected_dimensions={dimensions}"
                )
            if "time" not in dataset.dimensions or not (
                0 <= source_index < len(dataset.dimensions["time"])
            ):
                raise ValueError(f"Invalid time record {source_index}: {path}")
            units = dataset["time"].getncattr("units")
            value = float(dataset["time"][source_index])
            if time_units is None:
                time_units = units
            if units != time_units or (previous_time is not None and value <= previous_time):
                raise ValueError(f"Hourly times are inconsistent or unordered: {path}")
            previous_time = value
            for name in current_variables:
                variable = _source_variable(dataset, name)
                signature = (str(variable.dtype), variable.dimensions)
                if name in SPECS and canonical_names(dataset):
                    expected = UnknownDiagnostic(dataset, name)
                    if signature != (str(expected.dtype), expected.dimensions):
                        raise ValueError(f"Canonical diagnostic schema differs: {path}:{name}")
                    if name == "gfs_forecast_reference_time" and variable.getncattr("units") != units:
                        raise ValueError(f"GFS reference time units differ: {path}")
                if name in signatures and signatures[name] != signature:
                    raise ValueError(f"Variable schema differs: {path}:{name}")
                signatures[name] = signature
                if name in FIELDS:
                    attrs = {key: variable.getncattr(key) for key in
                             ("units", "scale_factor", "add_offset", "_FillValue")
                             if key in variable.ncattrs()}
                    if name in metadata:
                        if attrs.keys() != metadata[name].keys():
                            raise ValueError(f"Physical metadata differs: {path}:{name}")
                        for key, value in attrs.items():
                            numeric = np.asarray(value).dtype.kind in "fci"
                            equal = np.array_equal(value, metadata[name][key], equal_nan=True) if numeric else np.array_equal(value, metadata[name][key])
                            if not equal:
                                raise ValueError(f"Physical metadata differs: {path}:{name}/{key}")
                    metadata[name] = attrs
    assert dimensions is not None and variables is not None
    return dimensions, variables


class _UnknownTiming:
    """Virtual legacy timing field: zero explicitly means unavailable, not a donor ID."""

    def __init__(self, reference):
        self.reference = reference

    def __getattr__(self, name):
        return getattr(self.reference, name)

    def __getitem__(self, key):
        return np.zeros(np.shape(self.reference[key]), dtype="u1")


def _source_variable(dataset, name):
    if name in SPECS and name not in dataset.variables:
        return UnknownDiagnostic(dataset, name)
    if name in SPECS and canonical_names(dataset):
        return CanonicalDiagnostic(dataset, name)
    if name == "precip_timing_source_id" and name not in dataset.variables:
        return _UnknownTiming(dataset["precip_source_id"])
    return dataset[name]


def create_daily_archive(
    paths: list[Path],
    destination: Path,
    day: date,
    *,
    expected_hours: int = 24,
    compression_level: int = 2,
    work_directory: Path | None = None,
    time_variable_overrides: dict[str, np.ndarray] | None = None,
    global_attributes: dict[str, Any] | None = None,
    verification: str = "full",
    fully_verified_overrides: set[str] | None = None,
    source_time_indices: list[int] | None = None,
    chunk_copy: bool = False,
    preserve_source_chunks: bool = False,
    normalize_precipitation_timing: bool = False,
) -> Path:
    """Combine ordered hourly NetCDF files and verify every stored value."""
    archive_started = perf_counter()
    paths = list(paths)
    explicit_indices = source_time_indices is not None
    indices = [0] * len(paths) if source_time_indices is None else list(source_time_indices)
    if len(indices) != len(paths):
        raise ValueError("source_time_indices must match paths")
    dimensions, variable_names = _validate_inputs(
        paths, expected_hours, indices, normalize_precipitation_timing
    )
    overrides = {} if time_variable_overrides is None else time_variable_overrides
    verify_overrides = set(overrides) if fully_verified_overrides is None else fully_verified_overrides
    if not verify_overrides <= set(overrides):
        raise ValueError("fully_verified_overrides must be a subset of override variables")
    if verification not in {"full", "targeted"}:
        raise ValueError("verification must be 'full' or 'targeted'")
    unknown = set(overrides) - set(variable_names)
    if unknown:
        raise ValueError(f"Override variables are absent from hourly inputs: {sorted(unknown)}")
    global_attributes = {**(global_attributes or {}), **_record_policy_attributes(paths, indices)}
    if set(SPECS) <= set(variable_names):
        global_attributes["baseline_schema_version"] = VERSION
    if chunk_copy and compression_level == 2 and not destination.exists():
        from hydro_ops.forcing.chunk_archive import UnsupportedArchive, assemble
        try:
            timing = assemble(paths, indices, destination, day,
                              destination.parent if work_directory is None else work_directory,
                              expected_hours=expected_hours, overrides=overrides,
                              global_attributes=global_attributes,
                              normalize_precipitation_timing=normalize_precipitation_timing,
                              preserve_source_chunks=preserve_source_chunks)
        except UnsupportedArchive as error:
            import logging
            logging.getLogger(__name__).warning('Chunk archive fallback: %s', error)
        else:
            manifest = {
                'created': datetime.now(UTC).isoformat(), 'day': day.isoformat(),
                'daily_file': str(destination),
                'compression': {'filter': 'deflate', 'level': compression_level, 'shuffle': True},
                'source_files': [{'path': str(p), **({'time_index': i} if explicit_indices else {}),
                                  'bytes': p.stat().st_size, 'mtime': p.stat().st_mtime}
                                 for p, i in zip(paths, indices, strict=True)],
                'verified': True, 'verification': verification,
                'overridden_time_variables': sorted(overrides),
                'fully_verified_overrides': sorted(overrides),
                'archive_writer': 'compressed_chunks', 'chunk_integrity_verified': True,
                'timing': timing,
            }
            manifest_path = destination.with_suffix(destination.suffix+'.manifest.json')
            part = manifest_path.with_suffix(manifest_path.suffix+'.part')
            part.write_text(json.dumps(manifest, indent=2, sort_keys=True)+'\n')
            part.replace(manifest_path)
            return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    publishing = destination.with_name(f"{destination.name}.part")
    publishing.unlink(missing_ok=True)
    work_root = destination.parent if work_directory is None else work_directory
    work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hydro_ops_daily_", dir=work_root) as temporary:
        partial = Path(temporary) / destination.name
        try:
            with Dataset(paths[0]) as first, Dataset(partial, "w", format="NETCDF4") as output:
                output.createDimension("time", expected_hours)
                for name, length in dimensions.items():
                    output.createDimension(name, length)
                output.setncatts(
                    {
                        **{name: first.getncattr(name) for name in first.ncattrs()},
                        "archive_period": day.isoformat(),
                        "archive_granularity": "daily",
                        "hourly_source_count": expected_hours,
                        "history": (
                            f"{datetime.now(UTC).isoformat()} daily archive created by hydro_ops"
                        ),
                        **({} if global_attributes is None else global_attributes),
                    }
                )
                for name in variable_names:
                    source = _source_variable(first, name)
                    fill_value = (
                        source.getncattr("_FillValue") if "_FillValue" in source.ncattrs() else None
                    )
                    if name in SPECS and fill_value is None and canonical_names(first):
                        fill_value = UnknownDiagnostic(first, name).getncattr("_FillValue")
                    options: dict[str, Any] = {}
                    chunks = _chunks(source, {"time": expected_hours, **dimensions})
                    if chunks:
                        options.update(
                            zlib=True,
                            complevel=compression_level,
                            shuffle=True,
                            chunksizes=chunks,
                        )
                    if fill_value is not None:
                        options["fill_value"] = fill_value
                    target = output.createVariable(name, source.dtype, source.dimensions, **options)
                    target.setncatts(_attributes(source))
                    if name == "precip_timing_source_id" and normalize_precipitation_timing:
                        target.long_name = "source supplying the within-block hourly timing pattern"
                        target.comment = "Zero means no separate within-block timing provenance (not reconciled or unavailable); legacy missing fields normalized to zero."
                    if "time" not in source.dimensions:
                        target[...] = source[...]
            with Dataset(partial, "a") as output:
                for index, (path, source_index) in enumerate(
                    zip(paths, indices, strict=True)
                ):
                    with Dataset(path) as source:
                        for name in variable_names:
                            variable = _source_variable(source, name)
                            if "time" not in variable.dimensions:
                                verify_static = verification == "full" or index == expected_hours - 1
                                if verify_static and _digest(variable[...]) != _digest(
                                    output[name][...]
                                ):
                                    raise ValueError(f"Static variable {name} differs: {path}")
                                continue
                            axis = variable.dimensions.index("time")
                            source_slice = [slice(None)] * variable.ndim
                            source_slice[axis] = source_index
                            target_slice = [slice(None)] * variable.ndim
                            target_slice[axis] = index
                            if name in overrides:
                                override = np.asanyarray(overrides[name])
                                expected_shape = list(variable.shape)
                                expected_shape[axis] = expected_hours
                                if list(override.shape) != expected_shape:
                                    raise ValueError(
                                        f"Override {name} has shape {override.shape}; "
                                        f"expected {tuple(expected_shape)}"
                                    )
                                override_slice = [slice(None)] * override.ndim
                                override_slice[axis] = index
                                output[name][tuple(target_slice)] = np.ma.masked_invalid(
                                    override[tuple(override_slice)]
                                )
                            else:
                                output[name][tuple(target_slice)] = variable[tuple(source_slice)]
                time = output.variables.get("time")
                if time is not None:
                    for attribute in ("begin_date", "begin_time", "end_date", "end_time"):
                        if attribute in time.ncattrs():
                            time.delncattr(attribute)
                for variable in output.variables.values():
                    extrema = {name for name in ("vmin", "vmax") if name in variable.ncattrs()}
                    if not extrema or not np.issubdtype(variable.dtype, np.number):
                        continue
                    values = np.ma.asarray(variable[...])
                    if values.count() == 0:
                        continue
                    if "vmin" in extrema:
                        variable.setncattr("vmin", np.asarray(values.min()).item())
                    if "vmax" in extrema:
                        variable.setncattr("vmax", np.asarray(values.max()).item())
            archive_written = perf_counter()
            with Dataset(partial) as output:
                for index, (path, source_index) in enumerate(
                    zip(paths, indices, strict=True)
                ):
                    with Dataset(path) as source:
                        for name in variable_names:
                            variable = _source_variable(source, name)
                            if "time" not in variable.dimensions:
                                continue
                            if (
                                verification == "targeted"
                                and name not in verify_overrides
                                and name != "time"
                                and index not in {0, expected_hours // 2, expected_hours - 1}
                            ):
                                continue
                            axis = variable.dimensions.index("time")
                            source_slice = [slice(None)] * variable.ndim
                            source_slice[axis] = source_index
                            target_slice = [slice(None)] * variable.ndim
                            target_slice[axis] = index
                            expected = (
                                np.ma.masked_invalid(
                                    np.asanyarray(overrides[name])[tuple(target_slice)].astype(
                                        output[name].dtype
                                    )
                                )
                                if name in overrides
                                else variable[tuple(source_slice)]
                            )
                            if _digest(expected) != _digest(output[name][tuple(target_slice)]):
                                raise RuntimeError(
                                    f"Daily archive verification failed: {path}:{name}"
                                )
            archive_verified = perf_counter()
            shutil.copyfile(partial, publishing)
            if publishing.stat().st_size != partial.stat().st_size:
                raise RuntimeError(f"Daily archive publication copy failed: {destination}")
            publishing.replace(destination)
        except Exception:
            publishing.unlink(missing_ok=True)
            raise
    manifest = {
        "created": datetime.now(UTC).isoformat(),
        "day": day.isoformat(),
        "daily_file": str(destination),
        "compression": {
            "filter": "deflate",
            "level": compression_level,
            "shuffle": True,
        },
        "source_files": [
            {
                "path": str(path),
                **({"time_index": index} if explicit_indices else {}),
                "bytes": path.stat().st_size,
                "mtime": path.stat().st_mtime,
            }
            for path, index in zip(paths, indices, strict=True)
        ],
        "verified": True,
        "overridden_time_variables": sorted(overrides),
        "verification": verification,
        "fully_verified_overrides": sorted(verify_overrides),
        "timing": {
            "write_seconds": archive_written - archive_started,
            "integrity_seconds": archive_verified - archive_written,
            "total_seconds": perf_counter() - archive_started,
        },
    }
    manifest_path = destination.with_suffix(destination.suffix + ".manifest.json")
    manifest_partial = manifest_path.with_suffix(manifest_path.suffix + ".part")
    manifest_partial.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    manifest_partial.replace(manifest_path)
    return destination


def daily_archive_is_current(paths: list[Path], destination: Path, day: date) -> bool:
    """Return whether a verified daily manifest still matches every hourly input."""
    manifest_path = destination.with_suffix(destination.suffix + ".manifest.json")
    if not destination.is_file() or not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    expected = [
        {"path": str(path), "bytes": path.stat().st_size, "mtime": path.stat().st_mtime}
        for path in paths
    ]
    return (
        manifest.get("verified") is True
        and manifest.get("day") == day.isoformat()
        and manifest.get("source_files") == expected
    )


def verified_daily_archive(destination: Path, day: date) -> bool:
    """Return whether a published daily file has a valid completed manifest."""
    manifest_path = destination.with_suffix(destination.suffix + ".manifest.json")
    if not destination.is_file() or destination.stat().st_size == 0 or not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return (
        manifest.get("verified") is True
        and manifest.get("day") == day.isoformat()
        and len(manifest.get("source_files", ())) == 24
    )
