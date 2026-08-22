"""Prepare and apply elevation-aware PRISM daily temperature constraints."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from datetime import datetime
from itertools import pairwise
from pathlib import Path

import numpy as np
import xarray as xr
from netCDF4 import Dataset

from hydro_ops.forcing.inventory import inspect_forcing_file
from hydro_ops.forcing.normalize import open_normalized_forcing
from hydro_ops.forcing.physics import (
    DEFAULT_LAPSE_RATE,
    adjust_temperature_range,
    temperature_at_elevation,
)
from hydro_ops.forcing.static_validation import validate_terrain_bundle
from hydro_ops.forcing.thermodynamic_hour import build_remap_command
from hydro_ops.forcing.weights import validate_weight_manifest


def _field(dataset: xr.Dataset, name: str) -> np.ndarray:
    values = np.asarray(dataset[name].squeeze(drop=True).values, dtype=np.float64)
    if values.ndim != 2:
        raise ValueError(f"{name!r} must reduce to a two-dimensional field")
    return values


def create_prism_temperature_constraints(
    minimum_path: Path,
    maximum_path: Path,
    source_elevation_path: Path,
    target_grid_path: Path,
    target_elevation_path: Path,
    weights_path: Path,
    output_path: Path,
    *,
    cdo: str = "cdo",
    work_directory: Path | None = None,
    lapse_rate: float = DEFAULT_LAPSE_RATE,
    validate_weights: bool = True,
    force: bool = False,
) -> Path:
    """Lapse-normalize, remap, and restore PRISM Tmin/Tmax on the NWM grid."""
    if output_path.exists() and not force:
        raise FileExistsError(f"Output exists; use --force to replace it: {output_path}")
    executable = shutil.which(cdo)
    if not executable:
        raise RuntimeError(f"CDO executable not found: {cdo}")
    if validate_weights:
        metadata = validate_weight_manifest(
            minimum_path, "prism_tmin", target_grid_path, weights_path
        )
        maximum_inventory = inspect_forcing_file(maximum_path, "prism_tmax")
        if not maximum_inventory.valid:
            raise ValueError(f"Invalid PRISM Tmax: {'; '.join(maximum_inventory.issues)}")
        if maximum_inventory.grid_fingerprint != metadata["source_grid_fingerprint"]:
            raise ValueError("PRISM Tmin/Tmax grids differ or weights are stale")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    scratch = output_path.parent if work_directory is None else work_directory
    scratch.mkdir(parents=True, exist_ok=True)
    with (
        open_normalized_forcing(minimum_path, "prism_tmin") as minimum,
        open_normalized_forcing(maximum_path, "prism_tmax") as maximum,
        xr.open_dataset(source_elevation_path, mask_and_scale=True) as source_terrain,
        tempfile.TemporaryDirectory(prefix="hydro_ops_prism_temperature_", dir=scratch) as temp,
    ):
        source_elevation = _field(source_terrain, "elevation")
        minimum_values = _field(minimum, "daily_minimum_temperature")
        maximum_values = _field(maximum, "daily_maximum_temperature")
        if minimum_values.shape != maximum_values.shape or minimum_values.shape != source_elevation.shape:
            raise ValueError("PRISM extrema and source elevation shapes differ")
        static_validation = validate_terrain_bundle(
            source_elevation_path,
            "elevation",
            minimum_values.shape,
            target_grid_path,
            target_elevation_path,
        )
        dimensions = minimum["daily_minimum_temperature"].squeeze(drop=True).dims
        reference = xr.Dataset(
            {
                "minimum_reference": (
                    dimensions,
                    temperature_at_elevation(
                        minimum_values, source_elevation, 0.0, lapse_rate=lapse_rate
                    ).astype(np.float32),
                ),
                "maximum_reference": (
                    dimensions,
                    temperature_at_elevation(
                        maximum_values, source_elevation, 0.0, lapse_rate=lapse_rate
                    ).astype(np.float32),
                ),
            },
            coords={name: minimum.coords[name] for name in dimensions},
        )
        reference_path = Path(temp) / "reference.nc"
        remapped_path = Path(temp) / "remapped.nc"
        reference.to_netcdf(reference_path)
        command = build_remap_command(
            executable, target_grid_path, weights_path, reference_path, remapped_path
        )
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as error:
            details = (error.stderr or error.stdout or "no CDO diagnostic").strip()
            raise RuntimeError(f"CDO PRISM remapping failed: {details}") from error
        with (
            xr.open_dataset(remapped_path, mask_and_scale=True) as remapped,
            xr.open_dataset(target_elevation_path, mask_and_scale=True) as target_terrain,
        ):
            elevation = _field(target_terrain, "elevation")
            target_minimum = temperature_at_elevation(
                _field(remapped, "minimum_reference"), 0.0, elevation, lapse_rate=lapse_rate
            )
            target_maximum = temperature_at_elevation(
                _field(remapped, "maximum_reference"), 0.0, elevation, lapse_rate=lapse_rate
            )
        with Dataset(target_grid_path) as grid:
            active = np.asarray(grid["active_domain"][:], dtype=bool)
            latitude = np.asarray(grid["lat"][:])
            longitude = np.asarray(grid["lon"][:])
        partial = output_path.with_name(f"{output_path.name}.part")
        partial.unlink(missing_ok=True)
        try:
            dataset = xr.Dataset(
                {
                    "prism_tmin": (("y", "x"), np.where(active, target_minimum, np.nan).astype(np.float32)),
                    "prism_tmax": (("y", "x"), np.where(active, target_maximum, np.nan).astype(np.float32)),
                },
                coords={"lat": (("y", "x"), latitude), "lon": (("y", "x"), longitude)},
                attrs={
                    "prism_minimum_file": str(minimum_path),
                    "prism_maximum_file": str(maximum_path),
                    "source_elevation": str(source_elevation_path),
                    "target_elevation": str(target_elevation_path),
                    "remapping_weights": str(weights_path),
                    "lapse_rate_k_m": lapse_rate,
                    "source_elevation_sha256": static_validation.source_elevation_fingerprint,
                    "target_elevation_sha256": static_validation.target_elevation_fingerprint,
                },
            )
            dataset.to_netcdf(
                partial,
                encoding={name: {"zlib": True, "complevel": 2} for name in dataset.data_vars},
            )
            partial.replace(output_path)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
    return output_path


def apply_daily_temperature_constraint(
    baseline_paths: list[Path],
    constraint_path: Path,
    output_path: Path,
    *,
    baseline_variable: str = "T2D_PRELIM",
    minimum_baseline_range: float = 0.5,
    scale_bounds: tuple[float, float] = (0.25, 4.0),
    force: bool = False,
) -> Path:
    """Apply one PRISM 12Z-to-12Z affine constraint to 24 complete hourly fields."""
    if len(baseline_paths) != 24:
        raise ValueError("A complete PRISM day requires exactly 24 hourly baseline files")
    if output_path.exists() and not force:
        raise FileExistsError(f"Output exists; use --force to replace it: {output_path}")
    temperatures: list[np.ndarray] = []
    times: list[datetime] = []
    for path in baseline_paths:
        with xr.open_dataset(path, mask_and_scale=True) as dataset:
            temperatures.append(_field(dataset, baseline_variable))
            times.append(datetime.fromisoformat(dataset.attrs["source_valid_time"]))
    order = np.argsort(times)
    times = [times[index] for index in order]
    temperatures = [temperatures[index] for index in order]
    for earlier, later in pairwise(times):
        if (later - earlier).total_seconds() != 3600:
            raise ValueError("Baseline hours are not contiguous")
    with xr.open_dataset(constraint_path, mask_and_scale=True) as constraints:
        prism_minimum = _field(constraints, "prism_tmin")
        prism_maximum = _field(constraints, "prism_tmax")
    baseline = np.stack(temperatures)
    valid_constraint = np.isfinite(prism_minimum) & np.isfinite(prism_maximum) & (prism_minimum <= prism_maximum)
    safe_minimum = np.where(valid_constraint, prism_minimum, np.nanmin(baseline, axis=0))
    safe_maximum = np.where(valid_constraint, prism_maximum, np.nanmax(baseline, axis=0))
    adjustment = adjust_temperature_range(
        baseline, safe_minimum, safe_maximum, axis=0,
        minimum_baseline_range=minimum_baseline_range, scale_bounds=scale_bounds,
    )
    corrected = np.where(valid_constraint[None, ...], adjustment.temperature, baseline)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial = output_path.with_name(f"{output_path.name}.part")
    partial.unlink(missing_ok=True)
    dataset = xr.Dataset(
        {
            "T2D": (("time", "y", "x"), corrected.astype(np.float32)),
            "temperature_midpoint_shift": (("y", "x"), adjustment.midpoint_shift.astype(np.float32)),
            "temperature_range_scale": (("y", "x"), adjustment.range_scale.astype(np.float32)),
            "temperature_constraint_qc": (
                ("y", "x"),
                (
                    (~valid_constraint).astype(np.uint8)
                    | (adjustment.used_midpoint_only.astype(np.uint8) << 1)
                    | (adjustment.scale_was_clipped.astype(np.uint8) << 2)
                ),
            ),
        },
        coords={
            "time": np.array(
                [value.replace(tzinfo=None) for value in times], dtype="datetime64[ns]"
            )
        },
        attrs={"prism_constraint": str(constraint_path), "window": "12Z-to-12Z"},
    )
    try:
        dataset.to_netcdf(
            partial,
            encoding={name: {"zlib": True, "complevel": 2} for name in dataset.data_vars},
        )
        partial.replace(output_path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return output_path
