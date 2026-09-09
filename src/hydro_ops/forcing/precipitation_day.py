"""Batch-remap and composite a complete day of hourly precipitation."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import numpy as np
import xarray as xr
from netCDF4 import Dataset

from hydro_ops.forcing.precipitation import (
    CNRFC_STAGE4_POLICY_START,
    SOURCE_IDS,
    PrecipitationQC,
    composite_precipitation,
    open_precipitation_candidate,
)
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
        lat_bounds = output.variables.get("latitude_bounds")
        if lat_bounds is None:
            lat_bounds = output.createVariable(
                "latitude_bounds", "f8", ("y", "x", "nv4"), zlib=True, complevel=1
            )
        lon_bounds = output.variables.get("longitude_bounds")
        if lon_bounds is None:
            lon_bounds = output.createVariable(
                "longitude_bounds", "f8", ("y", "x", "nv4"), zlib=True, complevel=1
            )
        lat_bounds.units = "degrees_north"
        lon_bounds.units = "degrees_east"
        lat_bounds[:] = latitude
        lon_bounds[:] = longitude
        output["latitude"].bounds = "latitude_bounds"
        output["longitude"].bounds = "longitude_bounds"


def _write_source_scrip(weights: Path, destination: Path) -> None:
    """Extract the immutable source grid from a CDO/SCRIP remapping operator."""
    names = (
        "grid_dims",
        "grid_center_lat",
        "grid_center_lon",
        "grid_corner_lat",
        "grid_corner_lon",
        "grid_imask",
    )
    with Dataset(weights) as source, Dataset(destination, "w") as output:
        output.createDimension("grid_size", source.dimensions["src_grid_size"].size)
        output.createDimension("grid_corners", source.dimensions["src_grid_corners"].size)
        output.createDimension("grid_rank", source.dimensions["src_grid_rank"].size)
        dimensions = {
            "grid_dims": ("grid_rank",),
            "grid_center_lat": ("grid_size",),
            "grid_center_lon": ("grid_size",),
            "grid_corner_lat": ("grid_size", "grid_corners"),
            "grid_corner_lon": ("grid_size", "grid_corners"),
            "grid_imask": ("grid_size",),
        }
        for name in names:
            original = source[f"src_{name}"]
            variable = output.createVariable(name, original.dtype, dimensions[name])
            for attribute in original.ncattrs():
                variable.setncattr(attribute, original.getncattr(attribute))
            variable[:] = original[:]
        output.title = "Source grid extracted from precomputed remapping weights"
        output.conventions = "SCRIP"


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


def _load_cnrf_mask(path: Path, shape: tuple[int, int]) -> np.ndarray:
    with Dataset(path) as dataset:
        mask = np.asarray(dataset["cnrfc_mask"][:], dtype=bool)
    if mask.shape != shape:
        raise ValueError(f"CNRFC mask {path} has shape {mask.shape}, expected {shape}")
    return mask


def reconcile_stage4_six_hour_block(
    output_paths: list[Path],
    constraint_depth: np.ndarray,
    cnrfc_mask: np.ndarray,
    *,
    constraint_path: Path,
    constraint_end: datetime,
) -> None:
    """Conserve a Stage-IV six-hour total while retaining the best hourly timing pattern."""
    if len(output_paths) != 6:
        raise ValueError("A Stage-IV reconciliation block must contain exactly six hours")
    constraint = np.asarray(constraint_depth, dtype=np.float32)
    mask = np.asarray(cnrfc_mask, dtype=bool)
    if constraint.shape != mask.shape:
        raise ValueError("Stage-IV constraint and CNRFC mask shapes differ")
    hourly: list[np.ndarray] = []
    timing_ids: list[np.ndarray] = []
    for path in output_paths:
        with Dataset(path) as dataset:
            rate = np.ma.filled(dataset["RAINRATE"][0], np.nan).astype(np.float32)
            hourly.append(np.maximum(rate * np.float32(3600.0), 0.0))
            timing_ids.append(np.asarray(dataset["precip_source_id"][0], dtype=np.uint8))
    proxy = np.stack(hourly)
    proxy_sum = np.nansum(proxy, axis=0, dtype=np.float32)
    constrained = mask & np.isfinite(constraint) & (constraint >= 0)
    fallback = constrained & (constraint > 0) & (~np.isfinite(proxy_sum) | (proxy_sum <= 0))
    wet = constrained & ~fallback & (proxy_sum > 0)
    reconciled = proxy.copy()
    reconciled[:, wet] *= (constraint[wet] / proxy_sum[wet])[None, :]
    reconciled[:, fallback] = constraint[fallback][None, :] / np.float32(6.0)
    reconciled[:, constrained & (constraint == 0)] = 0.0
    # Put float32 rounding residual into the final interval so the stored hourly depths
    # reproduce the trusted six-hour total as closely as the output precision permits.
    residual = constraint - np.sum(reconciled[:5], axis=0, dtype=np.float32)
    reconciled[5, constrained] = np.maximum(residual[constrained], 0.0)
    for index, path in enumerate(output_paths):
        with Dataset(path, "a") as dataset:
            chunks = dataset["precip_source_id"].chunking()
            chunksizes = None if chunks == "contiguous" else tuple(chunks)
            timing = dataset.variables.get("precip_timing_source_id")
            if timing is None:
                timing = dataset.createVariable(
                    "precip_timing_source_id", "u1", ("time", "y", "x"),
                    zlib=True, complevel=2, shuffle=True, chunksizes=chunksizes,
                )
                timing.setncatts(
                    {
                        "long_name": "source supplying the within-block hourly timing pattern",
                        "flag_values": np.array([0, *SOURCE_IDS.values()], dtype=np.uint8),
                        "flag_meanings": "missing mrms_pass2 mrms_pass1 stage4_archive stage4_realtime nldas2 hrrr stage4_06h_constrained",
                    }
                )
                timing[:] = 0
            timing_values = np.asarray(timing[0], dtype=np.uint8)
            timing_values[constrained] = timing_ids[index][constrained]
            timing[0] = timing_values
            rate = np.ma.filled(dataset["RAINRATE"][0], np.nan).astype(np.float32)
            rate[constrained] = reconciled[index, constrained] / np.float32(3600.0)
            dataset["RAINRATE"][0] = rate
            source = np.asarray(dataset["precip_source_id"][0])
            source[constrained] = SOURCE_IDS["stage4_06h_constrained"]
            dataset["precip_source_id"][0] = source
            confidence = np.asarray(dataset["precip_confidence"][0])
            confidence[constrained] = 0.85
            dataset["precip_confidence"][0] = confidence
            qc = np.asarray(dataset["precip_qc_flags"][0], dtype=np.uint16)
            qc[constrained] |= np.uint16(PrecipitationQC.CNRFC_SIX_HOUR_CONSTRAINED)
            qc[fallback] |= np.uint16(PrecipitationQC.CNRFC_TIMING_FALLBACK)
            dataset["precip_qc_flags"][0] = qc
            dataset.setncatts(
                {
                    "cnrfc_stage4_policy": "hourly Stage-IV rejected; six-hour total imposed using composite hourly proportions",
                    "stage4_six_hour_constraint_file": str(constraint_path),
                    "stage4_six_hour_constraint_end": constraint_end.astimezone(UTC).isoformat(),
                }
            )


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
    stage4_six_hour_paths: dict[datetime, Path] | None = None,
    stage4_six_hour_weights: Path | None = None,
    cnrfc_mask_path: Path | None = None,
    cnrfc_policy_start: datetime = CNRFC_STAGE4_POLICY_START,
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
    products = set().union(*(set(hour) for hour in candidate_hours))
    if not products or any(not hour for hour in candidate_hours):
        raise ValueError("Every batch hour must have at least one precipitation candidate")
    if products != set(weight_paths):
        raise ValueError("Every batch candidate requires exactly one weight matrix")
    has_quality = any(path is not None for path in quality_hours)
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

    constraints = stage4_six_hour_paths or {}
    if constraints and (stage4_six_hour_weights is None or cnrfc_mask_path is None):
        raise ValueError("Stage-IV six-hour constraints require weights and a CNRFC mask")
    items = sorted(products)
    if has_quality:
        items.append("mrms_quality")
    if constraints:
        items.append("stage4_06h")
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
            if product == "mrms_quality":
                available = [
                    (valid, path)
                    for valid, path in zip(valid_times, quality_hours, strict=True)
                    if path is not None
                ]
            elif product == "stage4_06h":
                available = sorted(constraints.items())
            else:
                available = [
                    (valid, hour[product])
                    for valid, hour in zip(valid_times, candidate_hours, strict=True)
                    if product in hour
                ]
            product_times = [valid for valid, _ in available]
            paths = [path for _, path in available]
            weights = (
                quality_weights if product == "mrms_quality"
                else stage4_six_hour_weights if product == "stage4_06h"
                else weight_paths[product]
            )
            assert weights is not None
            if validate_weights:
                validation_product = (
                    "stage4_archive" if product == "stage4_06h" and "/archive/" in str(paths[0])
                    else "stage4_realtime" if product == "stage4_06h"
                    else product
                )
                validate_weight_manifest(
                    paths[0],
                    validation_product,
                    target_grid_path,
                    weights,
                    expected_method="bilinear" if product == "mrms_quality" else "conservative",
                )
            native = temp / f"{product}.native.nc"
            remapped = temp / f"{product}.remapped.nc"
            variable = _write_native_day(paths, product, product_times, native)
            if product.startswith("stage4_"):
                _attach_source_corners(native, weights)
            command = build_remap_command(
                executable, remap_grid_path, weights, native, remapped
            )
            if product.startswith("stage4_"):
                source_grid = temp / "stage4.source_scrip.nc"
                if not source_grid.exists():
                    _write_source_scrip(weights, source_grid)
                command.insert(-2, f"-setgrid,{source_grid}")
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
            cnrfc_mask: np.ndarray | None = None
            if cnrfc_mask_path is not None and valid_times[-1] >= cnrfc_policy_start:
                first_product = next(iter(candidate_hours[0]))
                sample_shape = _target_field(
                    datasets[first_product], variables[first_product], valid_times[0]
                ).shape
                cnrfc_mask = _load_cnrf_mask(cnrfc_mask_path, sample_shape)
            for index, valid_time in enumerate(valid_times):
                remapped_values = {
                    product: _target_field(datasets[product], variables[product], valid_time)
                    for product in candidate_hours[index]
                }
                quality = (
                    _target_field(datasets["mrms_quality"], "quality", valid_time)
                    if quality_hours[index] is not None
                    else None
                )
                composite = composite_precipitation(
                    remapped_values,
                    mrms_quality=quality,
                    mrms_quality_threshold=mrms_quality_threshold,
                    stage4_exclusion=(
                        cnrfc_mask
                        if cnrfc_mask is not None and valid_time >= cnrfc_policy_start
                        else None
                    ),
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
            if constraints:
                assert cnrfc_mask is not None
                index_by_time = {valid: index for index, valid in enumerate(valid_times)}
                for end, constraint_path in sorted(constraints.items()):
                    block = [end - timedelta(hours=offset) for offset in range(5, -1, -1)]
                    if end < cnrfc_policy_start or any(hour not in index_by_time for hour in block):
                        continue
                    reconcile_stage4_six_hour_block(
                        [outputs[index_by_time[hour]] for hour in block],
                        _target_field(datasets["stage4_06h"], "precipitation_depth", end),
                        cnrfc_mask,
                        constraint_path=constraint_path,
                        constraint_end=end,
                    )
        finally:
            for dataset in datasets.values():
                dataset.close()
    return outputs
