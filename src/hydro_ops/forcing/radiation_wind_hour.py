"""Produce one shortwave and earth-relative wind hour on the NWM grid."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from enum import IntFlag
from pathlib import Path

import numpy as np
import xarray as xr
from netCDF4 import Dataset, date2num

from hydro_ops.forcing.normalize import open_normalized_forcing
from hydro_ops.forcing.physics import (
    cosine_solar_zenith,
    lambert_grid_x_angle,
    rotate_grid_to_earth,
)
from hydro_ops.forcing.thermodynamic_hour import build_remap_command
from hydro_ops.forcing.weights import validate_weight_manifest


class RadiationWindQC(IntFlag):
    """Per-cell quality-control flags for shortwave and wind processing."""

    SHORTWAVE_NEGATIVE_CLIPPED = 1
    SHORTWAVE_NIGHT_ZEROED = 2
    INVALID_INPUT = 4
    SHORTWAVE_HIGH = 8


def _field(dataset: xr.Dataset, name: str) -> np.ndarray:
    values = np.asarray(dataset[name].squeeze(drop=True).values, dtype=np.float64)
    if values.ndim != 2:
        raise ValueError(f"{name!r} must reduce to one two-dimensional hourly field")
    return values


def _valid_time(source: xr.Dataset) -> datetime:
    value = source.attrs.get("source_valid_time")
    if not value or value == "unknown":
        raise ValueError("Source file has no valid hourly time")
    return datetime.fromisoformat(value)


def _write_native_fields(
    path: Path,
    source: xr.Dataset,
    *,
    shortwave_negative_tolerance: float,
) -> None:
    shortwave = _field(source, "downward_shortwave")
    grid_u = _field(source, "wind_u")
    grid_v = _field(source, "wind_v")
    finite_sw = np.isfinite(shortwave)
    if np.any(shortwave[finite_sw] < -shortwave_negative_tolerance):
        raise ValueError("Source shortwave radiation is materially negative")
    shortwave = np.where(finite_sw, np.maximum(shortwave, 0.0), np.nan)
    if source.attrs["wind_orientation"] == "grid_relative":
        angle = lambert_grid_x_angle(
            source["longitude"].values,
            central_longitude=-97.5,
            standard_parallel_1=38.5,
            standard_parallel_2=38.5,
        )
        eastward, northward = rotate_grid_to_earth(grid_u, grid_v, angle)
    elif source.attrs["wind_orientation"] == "earth_relative":
        eastward, northward = grid_u, grid_v
    else:
        raise ValueError(f"Unknown wind orientation {source.attrs['wind_orientation']!r}")
    invalid_vector = ~(np.isfinite(eastward) & np.isfinite(northward))
    eastward = np.where(invalid_vector, np.nan, eastward)
    northward = np.where(invalid_vector, np.nan, northward)
    source_field = source["downward_shortwave"].squeeze(drop=True)
    dimensions = source_field.dims
    coordinates = {name: source.coords[name] for name in dimensions if name in source.coords}
    for name in ("latitude", "longitude"):
        if name in source.coords:
            coordinates[name] = source.coords[name]
    output = xr.Dataset(
        {
            "shortwave": (dimensions, shortwave.astype(np.float32)),
            "eastward_wind": (dimensions, eastward.astype(np.float32)),
            "northward_wind": (dimensions, northward.astype(np.float32)),
            "source_shortwave_negative": (
                dimensions,
                (finite_sw & (_field(source, "downward_shortwave") < 0)).astype(np.float32),
            ),
        },
        coords=coordinates,
    )
    encoding = {name: {"zlib": True, "complevel": 1, "shuffle": True} for name in output.data_vars}
    output.to_netcdf(path, encoding=encoding)


def _write_output(
    path: Path,
    remapped_path: Path,
    target_grid_path: Path,
    *,
    source: xr.Dataset,
    weights_path: Path,
    solar_elevation_tolerance_degrees: float,
    maximum_shortwave: float,
) -> None:
    with xr.open_dataset(remapped_path, mask_and_scale=True) as remapped:
        shortwave = _field(remapped, "shortwave")
        eastward = _field(remapped, "eastward_wind")
        northward = _field(remapped, "northward_wind")
        negative_fraction = _field(remapped, "source_shortwave_negative")
    valid_time = _valid_time(source)
    with Dataset(target_grid_path) as grid:
        active = np.asarray(grid["active_domain"][:], dtype=bool)
        latitude = np.asarray(grid["lat"][:], dtype=np.float64)
        longitude = np.asarray(grid["lon"][:], dtype=np.float64)
        shape = active.shape
        for name, values in (
            ("shortwave", shortwave),
            ("eastward wind", eastward),
            ("northward wind", northward),
        ):
            if values.shape != shape:
                raise ValueError(f"Remapped {name} shape {values.shape} differs from target {shape}")
        cosine_zenith = cosine_solar_zenith(valid_time, latitude, longitude)
        horizon = np.sin(np.deg2rad(solar_elevation_tolerance_degrees))
        nighttime = cosine_zenith <= horizon
        qc = np.zeros(shape, dtype=np.uint16)
        qc[negative_fraction > 0] |= np.uint16(RadiationWindQC.SHORTWAVE_NEGATIVE_CLIPPED)
        qc[nighttime & (shortwave > 0)] |= np.uint16(RadiationWindQC.SHORTWAVE_NIGHT_ZEROED)
        shortwave = np.where(nighttime, 0.0, shortwave)
        qc[shortwave > maximum_shortwave] |= np.uint16(RadiationWindQC.SHORTWAVE_HIGH)
        invalid = ~(
            np.isfinite(shortwave) & np.isfinite(eastward) & np.isfinite(northward)
        )
        qc[invalid] |= np.uint16(RadiationWindQC.INVALID_INPUT)
        partial = path.with_name(f"{path.name}.part")
        partial.unlink(missing_ok=True)
        try:
            with Dataset(partial, "w", format="NETCDF4") as output:
                ny, nx = shape
                output.createDimension("time", 1)
                output.createDimension("y", ny)
                output.createDimension("x", nx)
                output.setncatts(
                    {
                        "Conventions": "CF-1.8",
                        "title": "Hourly shortwave and earth-relative wind on the NWM CONUS grid",
                        "source_product": source.attrs["source_product"],
                        "source_file": source.attrs["source_file"],
                        "source_valid_time": source.attrs["source_valid_time"],
                        "source_grid_fingerprint": source.attrs["source_grid_fingerprint"],
                        "source_wind_orientation": source.attrs["wind_orientation"],
                        "output_wind_orientation": "earth_relative",
                        "remapping_weights": str(weights_path),
                        "target_grid": str(target_grid_path),
                        "solar_elevation_tolerance_degrees": solar_elevation_tolerance_degrees,
                        "maximum_shortwave_w_m2": maximum_shortwave,
                        "history": f"{datetime.now(UTC).isoformat()} wind rotation and solar-checked remapping",
                    }
                )
                time = output.createVariable("time", "f8", ("time",))
                time.units = "seconds since 1970-01-01 00:00:00 UTC"
                time.calendar = "standard"
                time[:] = date2num(valid_time, time.units, time.calendar)
                chunks = (1, min(240, ny), min(288, nx))
                coordinate_chunks = (min(240, ny), min(288, nx))
                for name, values, units in (
                    ("lat", latitude, "degrees_north"),
                    ("lon", longitude, "degrees_east"),
                ):
                    variable = output.createVariable(
                        name, "f4", ("y", "x"), zlib=True, complevel=2,
                        shuffle=True, chunksizes=coordinate_chunks,
                    )
                    variable[:] = values
                    variable.units = units
                definitions = {
                    "SWDOWN": (shortwave, "W m-2", "surface downward shortwave radiation"),
                    "U2D": (eastward, "m s-1", "eastward 10 m wind"),
                    "V2D": (northward, "m s-1", "northward 10 m wind"),
                    "COSZEN": (cosine_zenith, "1", "cosine of solar zenith angle"),
                }
                for name, (values, units, long_name) in definitions.items():
                    variable = output.createVariable(
                        name, "f4", ("time", "y", "x"), fill_value=np.float32(9.96921e36),
                        zlib=True, complevel=2, shuffle=True, chunksizes=chunks,
                    )
                    variable.setncatts({"units": units, "long_name": long_name, "coordinates": "lat lon"})
                    variable[0] = np.ma.masked_where(~active | ~np.isfinite(values), values)
                flags = output.createVariable(
                    "radiation_wind_qc_flags", "u2", ("time", "y", "x"),
                    zlib=True, complevel=2, shuffle=True, chunksizes=chunks,
                )
                flags.setncatts(
                    {
                        "long_name": "shortwave and wind quality-control bit mask",
                        "flag_masks": np.array([1, 2, 4, 8], dtype=np.uint16),
                        "flag_meanings": (
                            "shortwave_negative_clipped shortwave_night_zeroed "
                            "invalid_input shortwave_high"
                        ),
                    }
                )
                flags[0] = np.where(active, qc, np.uint16(RadiationWindQC.INVALID_INPUT))
            partial.replace(path)
        except Exception:
            partial.unlink(missing_ok=True)
            raise


def process_radiation_wind_hour(
    source_path: Path,
    product: str,
    target_grid_path: Path,
    weights_path: Path,
    output_path: Path,
    *,
    cdo: str = "cdo",
    work_directory: Path | None = None,
    shortwave_negative_tolerance: float = 0.1,
    solar_elevation_tolerance_degrees: float = -0.833,
    maximum_shortwave: float = 1400.0,
    validate_weights: bool = True,
    force: bool = False,
) -> Path:
    """Rotate if needed, remap, and validate shortwave and paired wind components."""
    if product not in {"nldas2", "hrrr"}:
        raise ValueError("The radiation/wind processor supports nldas2 and hrrr")
    if output_path.exists() and not force:
        raise FileExistsError(f"Output exists; use --force to replace it: {output_path}")
    executable = shutil.which(cdo)
    if not executable:
        raise RuntimeError(f"CDO executable not found: {cdo}")
    for required in (source_path, target_grid_path, weights_path):
        if not required.is_file():
            raise FileNotFoundError(required)
    if validate_weights:
        validate_weight_manifest(source_path, product, target_grid_path, weights_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    scratch_parent = output_path.parent if work_directory is None else work_directory
    scratch_parent.mkdir(parents=True, exist_ok=True)
    with (
        open_normalized_forcing(source_path, product) as source,
        tempfile.TemporaryDirectory(
            prefix="hydro_ops_radiation_wind_", dir=scratch_parent
        ) as temporary,
    ):
        native_path = Path(temporary) / "native.nc"
        remapped_path = Path(temporary) / "remapped.nc"
        _write_native_fields(
            native_path,
            source,
            shortwave_negative_tolerance=shortwave_negative_tolerance,
        )
        command = build_remap_command(
            executable, target_grid_path, weights_path, native_path, remapped_path
        )
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as error:
            details = (error.stderr or error.stdout or "no CDO diagnostic").strip()
            raise RuntimeError(f"CDO remapping failed: {details}") from error
        _write_output(
            output_path,
            remapped_path,
            target_grid_path,
            source=source,
            weights_path=weights_path,
            solar_elevation_tolerance_degrees=solar_elevation_tolerance_degrees,
            maximum_shortwave=maximum_shortwave,
        )
    return output_path
