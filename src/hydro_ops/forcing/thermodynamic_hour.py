"""Produce one coupled thermodynamic forcing hour on the NWM grid."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import xarray as xr
from netCDF4 import Dataset, date2num

from hydro_ops.forcing.normalize import open_normalized_forcing
from hydro_ops.forcing.static_validation import StaticValidation, validate_terrain_bundle
from hydro_ops.forcing.thermodynamics import (
    ThermodynamicQC,
    finalize_target_state,
    prepare_reference_state,
)
from hydro_ops.forcing.weights import validate_weight_manifest

REFERENCE_VARIABLES = (
    "reference_temperature",
    "reference_pressure",
    "relative_humidity",
    "longwave_factor",
)
QC_FRACTION_VARIABLES = ("source_rh_clipped_low", "source_rh_clipped_high")


def build_remap_command(
    executable: str, target_grid: Path, weights: Path, source: Path, output: Path
) -> list[str]:
    """Build the CDO command that applies one precomputed weight matrix."""
    return [
        executable,
        "-O",
        f"remap,{target_grid},{weights}",
        str(source),
        str(output),
    ]


def _field(dataset: xr.Dataset, name: str) -> np.ndarray:
    values = np.asarray(dataset[name].squeeze(drop=True).values, dtype=np.float64)
    if values.ndim != 2:
        raise ValueError(f"{name!r} must reduce to one two-dimensional hourly field")
    return values


def _write_reference_file(
    path: Path,
    source: xr.Dataset,
    source_elevation: np.ndarray,
    relative_humidity_tolerance: float,
) -> None:
    state = prepare_reference_state(
        _field(source, "air_temperature"),
        _field(source, "surface_pressure"),
        _field(source, "specific_humidity"),
        _field(source, "downward_longwave"),
        source_elevation,
        relative_humidity_tolerance=relative_humidity_tolerance,
    )
    source_field = source["air_temperature"].squeeze(drop=True)
    dimensions = source_field.dims
    coordinates = {name: source.coords[name] for name in dimensions if name in source.coords}
    for name in ("latitude", "longitude"):
        if name in source.coords:
            coordinates[name] = source.coords[name]
    fields = {
        name: (dimensions, values.astype(np.float32))
        for name, values in zip(
            REFERENCE_VARIABLES,
            (
                state.temperature,
                state.pressure,
                state.relative_humidity,
                state.longwave_factor,
            ),
            strict=True,
        )
    }
    fields.update(
        {
            "source_rh_clipped_low": (
                dimensions,
                ((state.qc_flags & ThermodynamicQC.RH_CLIPPED_LOW) != 0).astype(np.float32),
            ),
            "source_rh_clipped_high": (
                dimensions,
                ((state.qc_flags & ThermodynamicQC.RH_CLIPPED_HIGH) != 0).astype(np.float32),
            ),
        }
    )
    output = xr.Dataset(
        fields,
        coords=coordinates,
        attrs={
            "source_product": source.attrs["source_product"],
            "source_file": source.attrs["source_file"],
            "source_grid_fingerprint": source.attrs["source_grid_fingerprint"],
            "source_valid_time": source.attrs["source_valid_time"],
            "reference_elevation_m": 0.0,
        },
    )
    encoding = {name: {"zlib": True, "complevel": 1, "shuffle": True} for name in output.data_vars}
    output.to_netcdf(path, encoding=encoding)


def _read_optional_temperature(
    path: Path, variable: str, valid_time: str
) -> np.ndarray:
    with xr.open_dataset(path, mask_and_scale=True) as dataset:
        field = dataset[variable]
        if "time" in field.dims and field.sizes["time"] > 1:
            requested = np.datetime64(datetime.fromisoformat(valid_time).replace(tzinfo=None))
            available = np.asarray(dataset["time"].values).astype("datetime64[ns]")
            matches = np.flatnonzero(available == requested.astype("datetime64[ns]"))
            if matches.size != 1:
                raise ValueError(f"Final temperature has no unique value for {valid_time}")
            field = field.isel(time=int(matches[0]))
        values = np.asarray(field.squeeze(drop=True).values, dtype=np.float64)
        if values.ndim != 2:
            raise ValueError(f"{variable!r} must reduce to one two-dimensional target field")
        return values


def _create_output(
    path: Path,
    remapped_path: Path,
    target_grid_path: Path,
    target_elevation_path: Path,
    *,
    source: xr.Dataset,
    weights_path: Path,
    final_temperature: np.ndarray | None,
    relative_humidity_tolerance: float,
    static_validation: StaticValidation,
) -> None:
    with xr.open_dataset(remapped_path, mask_and_scale=True) as remapped:
        reference = {name: _field(remapped, name) for name in REFERENCE_VARIABLES}
        qc_fractions = {name: _field(remapped, name) for name in QC_FRACTION_VARIABLES}
    with xr.open_dataset(target_elevation_path, mask_and_scale=True) as terrain:
        target_elevation = _field(terrain, "elevation")
    with Dataset(target_grid_path) as grid:
        active = np.asarray(grid["active_domain"][:], dtype=bool)
        latitude = grid["lat"]
        longitude = grid["lon"]
        shape = active.shape
        if target_elevation.shape != shape:
            raise ValueError("Target elevation and target grid shapes differ")
        for name, values in reference.items():
            if values.shape != shape:
                raise ValueError(f"Remapped {name} shape {values.shape} differs from target {shape}")
        if final_temperature is not None and final_temperature.shape != shape:
            raise ValueError("Final temperature and target grid shapes differ")
        state = finalize_target_state(
            reference["reference_temperature"],
            reference["reference_pressure"],
            reference["relative_humidity"],
            reference["longwave_factor"],
            target_elevation,
            final_temperature=final_temperature,
            relative_humidity_tolerance=relative_humidity_tolerance,
        )
        qc_flags = state.qc_flags.copy()
        qc_flags[qc_fractions["source_rh_clipped_low"] > 0] |= np.uint16(
            ThermodynamicQC.RH_CLIPPED_LOW
        )
        qc_flags[qc_fractions["source_rh_clipped_high"] > 0] |= np.uint16(
            ThermodynamicQC.RH_CLIPPED_HIGH
        )
        path.parent.mkdir(parents=True, exist_ok=True)
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
                        "title": "Coupled hourly thermodynamic forcing on the NWM CONUS grid",
                        "source_product": source.attrs["source_product"],
                        "source_file": source.attrs["source_file"],
                        "source_valid_time": source.attrs["source_valid_time"],
                        "source_grid_fingerprint": source.attrs["source_grid_fingerprint"],
                        "remapping_weights": str(weights_path),
                        "target_grid": str(target_grid_path),
                        "target_elevation": str(target_elevation_path),
                        "source_elevation_sha256": static_validation.source_elevation_fingerprint,
                        "target_elevation_sha256": static_validation.target_elevation_fingerprint,
                        "temperature_constraint_applied": "yes" if final_temperature is not None else "no",
                        "history": f"{datetime.now(UTC).isoformat()} coupled elevation-aware processing",
                    }
                )
                valid_time = datetime.fromisoformat(source.attrs["source_valid_time"])
                time = output.createVariable("time", "f8", ("time",))
                time.units = "seconds since 1970-01-01 00:00:00 UTC"
                time.calendar = "standard"
                time[:] = date2num(valid_time, time.units, time.calendar)
                chunks = (1, min(240, ny), min(288, nx))
                coordinate_chunks = (min(240, ny), min(288, nx))
                for name, original in (("lat", latitude), ("lon", longitude)):
                    variable = output.createVariable(
                        name, "f4", ("y", "x"), zlib=True, complevel=2,
                        shuffle=True, chunksizes=coordinate_chunks,
                    )
                    variable[:] = original[:]
                    variable.setncatts({key: original.getncattr(key) for key in original.ncattrs()})
                definitions = {
                    "T2D": (state.temperature, "K", "2 m air temperature"),
                    "PSFC": (state.pressure, "Pa", "surface pressure"),
                    "Q2D": (state.specific_humidity, "kg kg-1", "2 m specific humidity"),
                    "LWDOWN": (state.downward_longwave, "W m-2", "downward longwave radiation"),
                    "T2D_PRELIM": (state.preliminary_temperature, "K", "temperature before external constraint"),
                    "RH2D": (state.relative_humidity, "1", "2 m relative humidity"),
                    "T_ELEV_ADJ": (state.elevation_temperature_adjustment, "K", "temperature elevation adjustment"),
                    "P_ELEV_ADJ": (state.elevation_pressure_adjustment, "Pa", "pressure elevation adjustment"),
                }
                for name, (values, units, long_name) in definitions.items():
                    variable = output.createVariable(
                        name, "f4", ("time", "y", "x"), fill_value=np.float32(9.96921e36),
                        zlib=True, complevel=2, shuffle=True, chunksizes=chunks,
                    )
                    variable.setncatts({"units": units, "long_name": long_name, "coordinates": "lat lon"})
                    variable[0] = np.ma.masked_where(~active | ~np.isfinite(values), values)
                qc = output.createVariable(
                    "thermodynamic_qc_flags", "u2", ("time", "y", "x"),
                    zlib=True, complevel=2, shuffle=True, chunksizes=chunks,
                )
                qc.setncatts({"long_name": "coupled thermodynamic quality-control bit mask"})
                qc[0] = np.where(active, qc_flags, np.uint16(ThermodynamicQC.INVALID_INPUT))
            partial.replace(path)
        except Exception:
            partial.unlink(missing_ok=True)
            raise


def process_thermodynamic_hour(
    source_path: Path,
    product: str,
    source_elevation_path: Path,
    source_elevation_variable: str,
    target_grid_path: Path,
    target_elevation_path: Path,
    weights_path: Path,
    output_path: Path,
    *,
    final_temperature_path: Path | None = None,
    final_temperature_variable: str = "T2D",
    cdo: str = "cdo",
    work_directory: Path | None = None,
    relative_humidity_tolerance: float = 0.10,
    validate_weights: bool = True,
    force: bool = False,
) -> Path:
    """Run the coupled reference-elevation/remap/target-elevation hourly workflow."""
    if product not in {"nldas2", "hrrr"}:
        raise ValueError("The coupled processor supports nldas2 and hrrr")
    if output_path.exists() and not force:
        raise FileExistsError(f"Output exists; use --force to replace it: {output_path}")
    executable = shutil.which(cdo)
    if not executable:
        raise RuntimeError(f"CDO executable not found: {cdo}")
    for required in (
        source_path, source_elevation_path, target_grid_path, target_elevation_path, weights_path
    ):
        if not required.is_file():
            raise FileNotFoundError(required)
    if validate_weights:
        validate_weight_manifest(source_path, product, target_grid_path, weights_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    scratch_parent = output_path.parent if work_directory is None else work_directory
    scratch_parent.mkdir(parents=True, exist_ok=True)
    with open_normalized_forcing(source_path, product) as source:
        with xr.open_dataset(source_elevation_path, mask_and_scale=True) as terrain:
            source_elevation = _field(terrain, source_elevation_variable)
        source_shape = _field(source, "air_temperature").shape
        if source_elevation.shape != source_shape:
            raise ValueError("Source elevation and hourly source grid shapes differ")
        static_validation = validate_terrain_bundle(
            source_elevation_path,
            source_elevation_variable,
            source_shape,
            target_grid_path,
            target_elevation_path,
        )
        final_temperature = (
            None
            if final_temperature_path is None
            else _read_optional_temperature(
                final_temperature_path,
                final_temperature_variable,
                source.attrs["source_valid_time"],
            )
        )
        with tempfile.TemporaryDirectory(
            prefix="hydro_ops_thermodynamic_", dir=scratch_parent
        ) as temporary:
            work = Path(temporary)
            reference_path = work / "reference.nc"
            remapped_path = work / "remapped.nc"
            _write_reference_file(
                reference_path, source, source_elevation, relative_humidity_tolerance
            )
            command = build_remap_command(
                executable, target_grid_path, weights_path, reference_path, remapped_path
            )
            try:
                subprocess.run(command, check=True, capture_output=True, text=True)
            except subprocess.CalledProcessError as error:
                details = (error.stderr or error.stdout or "no CDO diagnostic").strip()
                raise RuntimeError(f"CDO remapping failed: {details}") from error
            _create_output(
                output_path, remapped_path, target_grid_path, target_elevation_path,
                source=source, weights_path=weights_path, final_temperature=final_temperature,
                relative_humidity_tolerance=relative_humidity_tolerance,
                static_validation=static_validation,
            )
    return output_path
