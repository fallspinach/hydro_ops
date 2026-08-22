"""Remap, composite, and write one hourly precipitation forcing field."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import xarray as xr
from netCDF4 import Dataset, date2num

from hydro_ops.forcing.precipitation import (
    SOURCE_IDS,
    composite_precipitation,
    open_precipitation_candidate,
)
from hydro_ops.forcing.thermodynamic_hour import build_remap_command
from hydro_ops.forcing.weights import validate_weight_manifest


def _field(path: Path, variable: str) -> np.ndarray:
    with xr.open_dataset(path, mask_and_scale=True) as dataset:
        values = np.asarray(dataset[variable].squeeze(drop=True).values, dtype=np.float64)
    if values.ndim != 2:
        raise ValueError(f"{path}:{variable} must reduce to a two-dimensional field")
    return values


def process_precipitation_hour(
    candidate_paths: dict[str, Path],
    weight_paths: dict[str, Path],
    target_grid_path: Path,
    output_path: Path,
    *,
    remap_grid_path: Path | None = None,
    quality_path: Path | None = None,
    quality_weights: Path | None = None,
    stage4_override_path: Path | None = None,
    mrms_quality_threshold: float = 0.5,
    cdo: str = "cdo",
    work_directory: Path | None = None,
    validate_weights: bool = True,
    force: bool = False,
) -> Path:
    """Conservatively remap candidates and select the best available target-cell source."""
    if not candidate_paths:
        raise ValueError("No precipitation candidates were supplied")
    if set(candidate_paths) != set(weight_paths):
        raise ValueError("Every precipitation candidate requires one weight matrix")
    if output_path.exists() and not force:
        raise FileExistsError(f"Output exists; use --force to replace it: {output_path}")
    executable = shutil.which(cdo)
    if not executable:
        raise RuntimeError(f"CDO executable not found: {cdo}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    remap_grid_path = target_grid_path if remap_grid_path is None else remap_grid_path
    if not remap_grid_path.is_file():
        raise FileNotFoundError(remap_grid_path)
    scratch = output_path.parent if work_directory is None else work_directory
    scratch.mkdir(parents=True, exist_ok=True)
    remapped_values: dict[str, np.ndarray] = {}
    valid_times: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="hydro_ops_precipitation_", dir=scratch) as temporary:
        temporary = Path(temporary)
        items = list(candidate_paths.items())
        if quality_path is not None:
            if quality_weights is None:
                raise ValueError("MRMS quality requires bilinear quality weights")
            items.append(("mrms_quality", quality_path))
        for product, source_path in items:
            weights = quality_weights if product == "mrms_quality" else weight_paths[product]
            if validate_weights:
                validate_weight_manifest(
                    source_path,
                    product,
                    target_grid_path,
                    weights,
                    expected_method="bilinear" if product == "mrms_quality" else "conservative",
                )
            with open_precipitation_candidate(source_path, product) as normalized:
                valid_times.add(normalized.attrs["valid_time"])
                variable = "quality" if product == "mrms_quality" else "precipitation_depth"
                native = temporary / f"{product}.nc"
                remapped = temporary / f"{product}.remapped.nc"
                normalized[[variable]].to_netcdf(native)
            command = build_remap_command(
                executable, remap_grid_path, weights, native, remapped
            )
            try:
                subprocess.run(command, check=True, capture_output=True, text=True)
            except subprocess.CalledProcessError as error:
                details = (error.stderr or error.stdout or "no CDO diagnostic").strip()
                raise RuntimeError(f"CDO {product} remapping failed: {details}") from error
            remapped_values[product] = _field(remapped, variable)
    if len(valid_times) != 1:
        raise ValueError(f"Candidate valid times differ: {sorted(valid_times)}")
    quality = remapped_values.pop("mrms_quality", None)
    override = None if stage4_override_path is None else _field(stage4_override_path, "stage4_override")
    composite = composite_precipitation(
        remapped_values,
        mrms_quality=quality,
        mrms_quality_threshold=mrms_quality_threshold,
        stage4_override=override,
    )
    valid_time_text = next(iter(valid_times))
    valid_time = datetime.fromisoformat(valid_time_text.removesuffix("Z"))
    with Dataset(target_grid_path) as grid:
        active = np.asarray(grid["active_domain"][:], dtype=bool)
        latitude = np.asarray(grid["lat"][:])
        longitude = np.asarray(grid["lon"][:])
    partial = output_path.with_name(f"{output_path.name}.part")
    partial.unlink(missing_ok=True)
    try:
        with Dataset(partial, "w", format="NETCDF4") as output:
            ny, nx = active.shape
            output.createDimension("time", 1)
            output.createDimension("bounds", 2)
            output.createDimension("y", ny)
            output.createDimension("x", nx)
            output.setncatts(
                {
                    "Conventions": "CF-1.8",
                    "title": "Quality-aware hourly precipitation on the NWM grid",
                    "source_files": json.dumps(
                        {name: str(path) for name, path in candidate_paths.items()}, sort_keys=True
                    ),
                    "quality_file": "none" if quality_path is None else str(quality_path),
                    "mrms_quality_threshold": mrms_quality_threshold,
                    "remap_grid": str(remap_grid_path),
                    "valid_time": valid_time_text,
                    "history": f"{datetime.now(UTC).isoformat()} precipitation compositing",
                }
            )
            time = output.createVariable("time", "f8", ("time",))
            time.units = "seconds since 1970-01-01 00:00:00 UTC"
            time.calendar = "standard"
            time[:] = date2num(valid_time, time.units, time.calendar)
            bounds = output.createVariable("time_bounds", "f8", ("time", "bounds"))
            bounds.units = time.units
            bounds[:] = [[time[0] - 3600, time[0]]]
            chunks2 = (min(240, ny), min(288, nx))
            chunks3 = (1, *chunks2)
            for name, values, units in (
                ("lat", latitude, "degrees_north"),
                ("lon", longitude, "degrees_east"),
            ):
                variable = output.createVariable(
                    name, "f4", ("y", "x"), zlib=True, complevel=2,
                    shuffle=True, chunksizes=chunks2,
                )
                variable[:] = values
                variable.units = units
            rain = output.createVariable(
                "RAINRATE", "f4", ("time", "y", "x"), fill_value=np.float32(-9.99e8),
                zlib=True, complevel=2, shuffle=True, chunksizes=chunks3,
            )
            rain.setncatts(
                {"standard_name": "precipitation_flux", "units": "kg/m^2/s",
                 "cell_methods": "time: mean", "coordinates": "lat lon"}
            )
            rain[0] = np.ma.masked_where(
                ~active | ~np.isfinite(composite.depth), composite.depth / 3600.0
            )
            source = output.createVariable(
                "precip_source_id", "u1", ("time", "y", "x"),
                zlib=True, complevel=2, shuffle=True, chunksizes=chunks3,
            )
            source.setncatts(
                {"flag_values": np.array([0, *SOURCE_IDS.values()], dtype=np.uint8),
                 "flag_meanings": "missing mrms_pass2 mrms_pass1 stage4_archive stage4_realtime nldas2 hrrr"}
            )
            source[0] = np.where(active, composite.source_id, 0)
            confidence = output.createVariable(
                "precip_confidence", "f4", ("time", "y", "x"),
                zlib=True, complevel=2, shuffle=True, chunksizes=chunks3,
            )
            confidence.units = "1"
            confidence[0] = np.where(active, composite.confidence, 0)
            qc = output.createVariable(
                "precip_qc_flags", "u2", ("time", "y", "x"),
                zlib=True, complevel=2, shuffle=True, chunksizes=chunks3,
            )
            qc[0] = np.where(active, composite.qc_flags, np.uint16(8))
        partial.replace(output_path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return output_path
