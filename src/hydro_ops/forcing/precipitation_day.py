"""Batch-remap and composite a complete day of hourly precipitation."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from itertools import pairwise
from pathlib import Path

import numpy as np
import xarray as xr
from netCDF4 import Dataset

from hydro_ops.forcing.precipitation import composite_precipitation, open_precipitation_candidate
from hydro_ops.forcing.precipitation_hour import write_precipitation_output
from hydro_ops.forcing.thermodynamic_hour import build_remap_command
from hydro_ops.forcing.weights import validate_weight_manifest


def _write_native_day(
    paths: list[Path], product: str, valid_times: list[datetime], destination: Path
) -> str:
    variable = "quality" if product == "mrms_quality" else "precipitation_depth"
    fields: list[xr.Dataset] = []
    try:
        for path, valid_time in zip(paths, valid_times, strict=True):
            normalized = open_precipitation_candidate(path, product, valid_time=valid_time)
            fields.append(normalized[[variable]].load())
            normalized.close()
        combined = xr.concat(fields, dim="time")
        available = np.asarray(combined["time"].values).astype("datetime64[ns]")
        expected = np.asarray(
            [value.replace(tzinfo=None) for value in valid_times], dtype="datetime64[ns]"
        )
        if not np.array_equal(available, expected):
            raise ValueError(f"{product} batch times differ from the requested hours")
        combined.attrs.update(
            {
                "source_product": product,
                "batch_hours": len(valid_times),
                "batch_start": valid_times[0].isoformat(),
                "batch_end": valid_times[-1].isoformat(),
            }
        )
        combined.to_netcdf(destination)
    finally:
        for field in fields:
            field.close()
    return variable


def _attach_source_corners(native: Path, weights: Path) -> None:
    """Restore curvilinear cell corners carried by the static SCRIP operator.

    Xarray preserves Stage-IV cell centers while concatenating hours, but CDO cannot always
    infer cell corners from that temporary curvilinear file.  The precomputed conservative
    operator contains the exact source corners used to create the weights, so reusing them is
    both deterministic and geometrically consistent with the operator.
    """
    with Dataset(weights) as operator:
        dimensions = np.asarray(operator["src_grid_dims"][:], dtype=np.int64)
        if dimensions.size != 2:
            raise ValueError(f"Unsupported source-grid rank in {weights}")
        x_size, y_size = (int(value) for value in dimensions)
        corners = operator.dimensions["src_grid_corners"].size
        latitude = np.rad2deg(
            np.asarray(operator["src_grid_corner_lat"][:], dtype=np.float64)
        ).reshape(y_size, x_size, corners)
        longitude = np.rad2deg(
            np.asarray(operator["src_grid_corner_lon"][:], dtype=np.float64)
        ).reshape(y_size, x_size, corners)
    with Dataset(native, "a") as output:
        if output.dimensions["y"].size != y_size or output.dimensions["x"].size != x_size:
            raise ValueError(f"Source-grid dimensions in {native} do not match {weights}")
        if "nv4" not in output.dimensions:
            output.createDimension("nv4", corners)
        lat_bounds = output.createVariable(
            "latitude_bounds", "f8", ("y", "x", "nv4"), zlib=True, complevel=1
        )
        lon_bounds = output.createVariable(
            "longitude_bounds", "f8", ("y", "x", "nv4"), zlib=True, complevel=1
        )
        lat_bounds.units = "degrees_north"
        lon_bounds.units = "degrees_east"
        lat_bounds[:] = latitude
        lon_bounds[:] = longitude
        output["latitude"].bounds = "latitude_bounds"
        output["longitude"].bounds = "longitude_bounds"


def _target_field(dataset: xr.Dataset, variable: str, valid_time: datetime) -> np.ndarray:
    requested = np.datetime64(valid_time.replace(tzinfo=None), "ns")
    available = np.asarray(dataset["time"].values).astype("datetime64[ns]")
    matches = np.flatnonzero(available == requested)
    if matches.size != 1:
        raise ValueError(f"Remapped batch has no unique value for {valid_time.isoformat()}")
    values = np.asarray(
        dataset[variable].isel(time=int(matches[0])).squeeze(drop=True).values,
        dtype=np.float64,
    )
    if values.ndim != 2:
        raise ValueError(f"Remapped {variable} is not a two-dimensional hourly field")
    return values


def process_precipitation_day(
    valid_times: list[datetime],
    candidate_hours: list[dict[str, Path]],
    quality_hours: list[Path | None],
    weight_paths: dict[str, Path],
    target_grid_path: Path,
    remap_grid_path: Path,
    output_directory: Path,
    *,
    quality_weights: Path | None = None,
    mrms_quality_threshold: float = 0.5,
    cdo: str = "cdo",
    work_directory: Path | None = None,
    validate_weights: bool = True,
    remap_workers: int = 1,
    force: bool = False,
) -> list[Path]:
    """Apply each static remapping operator once to a contiguous multi-hour batch."""
    if not valid_times or len(valid_times) != len(candidate_hours):
        raise ValueError("Valid times and candidate-hour mappings must be nonempty and equal")
    if remap_workers <= 0:
        raise ValueError("remap_workers must be positive")
    if len(quality_hours) != len(valid_times):
        raise ValueError("Quality-hour paths must align with valid times")
    for earlier, later in pairwise(valid_times):
        if (later - earlier).total_seconds() != 3600:
            raise ValueError("Batch valid times must be chronological and contiguous")
    products = set(candidate_hours[0])
    if not products or any(set(hour) != products for hour in candidate_hours):
        raise ValueError("Every batch hour must have the same precipitation candidates")
    if products != set(weight_paths):
        raise ValueError("Every batch candidate requires exactly one weight matrix")
    has_quality = all(path is not None for path in quality_hours)
    if any(path is not None for path in quality_hours) != has_quality:
        raise ValueError("MRMS quality must be present for every batch hour or none")
    if has_quality and quality_weights is None:
        raise ValueError("MRMS quality requires bilinear quality weights")
    executable = shutil.which(cdo)
    if not executable:
        raise RuntimeError(f"CDO executable not found: {cdo}")
    output_directory.mkdir(parents=True, exist_ok=True)
    work_root = output_directory if work_directory is None else work_directory
    work_root.mkdir(parents=True, exist_ok=True)
    outputs = [output_directory / f"{valid:%Y%m%d%H}.precipitation.nc" for valid in valid_times]
    if not force and any(path.exists() for path in outputs):
        raise FileExistsError("One or more batch precipitation outputs already exist; use --force")

    items = sorted(products)
    if has_quality:
        items.append("mrms_quality")
    with tempfile.TemporaryDirectory(prefix="hydro_ops_precipitation_day_", dir=work_root) as temp:
        temp = Path(temp)
        remapped_paths: dict[str, Path] = {}
        variables: dict[str, str] = {}
        commands: list[tuple[str, list[str], Path]] = []

        def run_remap(item: tuple[str, list[str], Path]) -> None:
            product, command, native = item
            try:
                subprocess.run(command, check=True, capture_output=True, text=True)
            except subprocess.CalledProcessError as error:
                details = (error.stderr or error.stdout or "no CDO diagnostic").strip()
                raise RuntimeError(f"CDO {product} batch remapping failed: {details}") from error
            native.unlink()

        for product in items:
            paths = (
                [path for path in quality_hours if path is not None]
                if product == "mrms_quality"
                else [hour[product] for hour in candidate_hours]
            )
            weights = quality_weights if product == "mrms_quality" else weight_paths[product]
            assert weights is not None
            if validate_weights:
                validate_weight_manifest(
                    paths[0],
                    product,
                    target_grid_path,
                    weights,
                    expected_method="bilinear" if product == "mrms_quality" else "conservative",
                )
            native = temp / f"{product}.native.nc"
            remapped = temp / f"{product}.remapped.nc"
            variable = _write_native_day(paths, product, valid_times, native)
            if product == "stage4_realtime":
                _attach_source_corners(native, weights)
            command = build_remap_command(
                executable, remap_grid_path, weights, native, remapped
            )
            item = (product, command, native)
            if remap_workers == 1:
                run_remap(item)
            else:
                commands.append(item)
            remapped_paths[product] = remapped
            variables[product] = variable

        if commands:
            with ThreadPoolExecutor(max_workers=min(remap_workers, len(commands))) as executor:
                list(executor.map(run_remap, commands))

        datasets = {
            product: xr.open_dataset(path, mask_and_scale=True)
            for product, path in remapped_paths.items()
        }
        try:
            for index, valid_time in enumerate(valid_times):
                remapped_values = {
                    product: _target_field(datasets[product], variables[product], valid_time)
                    for product in products
                }
                quality = (
                    _target_field(datasets["mrms_quality"], "quality", valid_time)
                    if has_quality
                    else None
                )
                composite = composite_precipitation(
                    remapped_values,
                    mrms_quality=quality,
                    mrms_quality_threshold=mrms_quality_threshold,
                )
                write_precipitation_output(
                    composite,
                    target_grid_path,
                    outputs[index],
                    valid_time=valid_time,
                    source_files=candidate_hours[index],
                    quality_file=quality_hours[index],
                    mrms_quality_threshold=mrms_quality_threshold,
                    remap_grid_path=remap_grid_path,
                )
                with Dataset(outputs[index], "a") as output:
                    output.setncattr("precipitation_remap_mode", "daily_batch")
        finally:
            for dataset in datasets.values():
                dataset.close()
    return outputs
