"""Atomic daily NetCDF collections for completed hourly forcing products."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
from netCDF4 import Dataset


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


def _validate_inputs(paths: list[Path], expected_hours: int) -> tuple[dict[str, int], list[str]]:
    if len(paths) != expected_hours:
        raise ValueError(f"Expected {expected_hours} hourly files, found {len(paths)}")
    dimensions: dict[str, int] | None = None
    variables: list[str] | None = None
    previous_time: float | None = None
    time_units: str | None = None
    for path in paths:
        with Dataset(path) as dataset:
            current_dimensions = {
                name: len(value) for name, value in dataset.dimensions.items() if name != "time"
            }
            current_variables = sorted(dataset.variables)
            if dimensions is None:
                dimensions, variables = current_dimensions, current_variables
            elif dimensions != current_dimensions or variables != current_variables:
                raise ValueError(f"Hourly NetCDF schema differs: {path}")
            if "time" not in dataset.dimensions or len(dataset.dimensions["time"]) != 1:
                raise ValueError(f"Expected exactly one time record: {path}")
            units = dataset["time"].getncattr("units")
            value = float(dataset["time"][0])
            if time_units is None:
                time_units = units
            if units != time_units or (previous_time is not None and value <= previous_time):
                raise ValueError(f"Hourly times are inconsistent or unordered: {path}")
            previous_time = value
    assert dimensions is not None and variables is not None
    return dimensions, variables


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
) -> Path:
    """Combine ordered hourly NetCDF files and verify every stored value."""
    paths = list(paths)
    dimensions, variable_names = _validate_inputs(paths, expected_hours)
    overrides = {} if time_variable_overrides is None else time_variable_overrides
    verify_overrides = set(overrides) if fully_verified_overrides is None else fully_verified_overrides
    if not verify_overrides <= set(overrides):
        raise ValueError("fully_verified_overrides must be a subset of override variables")
    if verification not in {"full", "targeted"}:
        raise ValueError("verification must be 'full' or 'targeted'")
    unknown = set(overrides) - set(variable_names)
    if unknown:
        raise ValueError(f"Override variables are absent from hourly inputs: {sorted(unknown)}")
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
                    source = first[name]
                    fill_value = (
                        source.getncattr("_FillValue") if "_FillValue" in source.ncattrs() else None
                    )
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
                    if "time" not in source.dimensions:
                        target[...] = source[...]
            with Dataset(partial, "a") as output:
                for index, path in enumerate(paths):
                    with Dataset(path) as source:
                        for name in variable_names:
                            variable = source[name]
                            if "time" not in variable.dimensions:
                                verify_static = verification == "full" or index == expected_hours - 1
                                if verify_static and _digest(variable[...]) != _digest(
                                    output[name][...]
                                ):
                                    raise ValueError(f"Static variable {name} differs: {path}")
                                continue
                            axis = variable.dimensions.index("time")
                            source_slice = [slice(None)] * variable.ndim
                            source_slice[axis] = 0
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
            with Dataset(partial) as output:
                for index, path in enumerate(paths):
                    with Dataset(path) as source:
                        for name in variable_names:
                            variable = source[name]
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
                            source_slice[axis] = 0
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
            {"path": str(path), "bytes": path.stat().st_size, "mtime": path.stat().st_mtime}
            for path in paths
        ],
        "verified": True,
        "overridden_time_variables": sorted(overrides),
        "verification": verification,
        "fully_verified_overrides": sorted(verify_overrides),
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
