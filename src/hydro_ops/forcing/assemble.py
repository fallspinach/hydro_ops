"""Assemble processed components into a seven-field hourly LDASIN file."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, date2num

VARIABLES = {
    "T2D": ("air_temperature", "Air Temperature", "K"),
    "Q2D": ("specific_humidity", "Specific Humidity", "1"),
    "PSFC": ("air_pressure", "Pressure", "Pa"),
    "U2D": ("eastward_wind", "U Wind", "m/s"),
    "V2D": ("northward_wind", "V Wind", "m/s"),
    "SWDOWN": (
        "surface_downwelling_shortwave_flux_in_air",
        "Downward Shortwave Radiation",
        "W/m^2",
    ),
    "LWDOWN": (
        "surface_downwelling_longwave_flux_in_air",
        "Downward Longwave Radiation",
        "W/m^2",
    ),
}
THERMODYNAMIC_VARIABLES = {"T2D", "Q2D", "PSFC", "LWDOWN"}
SOURCE_IDS = {"nldas2": 1, "hrrr": 2}


def assemble_seven_field_hour(
    thermodynamic_path: Path,
    radiation_wind_path: Path,
    target_grid_path: Path,
    output_path: Path,
    *,
    fallback_used: bool = False,
    force: bool = False,
    rows_per_chunk: int = 120,
) -> Path:
    """Create one schema-compatible hour while withholding not-yet-produced precipitation."""
    if output_path.exists() and not force:
        raise FileExistsError(f"Output exists; use --force to replace it: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial = output_path.with_name(f"{output_path.name}.part")
    partial.unlink(missing_ok=True)
    with (
        Dataset(thermodynamic_path) as thermo,
        Dataset(radiation_wind_path) as radiation_wind,
        Dataset(target_grid_path) as grid,
    ):
        thermo_time = thermo.getncattr("source_valid_time")
        radiation_time = radiation_wind.getncattr("source_valid_time")
        thermo_product = thermo.getncattr("source_product")
        radiation_product = radiation_wind.getncattr("source_product")
        if thermo_time != radiation_time:
            raise ValueError("Component valid times differ")
        if thermo_product != radiation_product:
            raise ValueError("Component products differ; whole-hour source consistency required")
        if thermo_product not in SOURCE_IDS:
            raise ValueError(f"Unknown forcing source {thermo_product!r}")
        ny, nx = len(grid.dimensions["y"]), len(grid.dimensions["x"])
        expected_shape = (1, ny, nx)
        for name in VARIABLES:
            source = thermo if name in THERMODYNAMIC_VARIABLES else radiation_wind
            if name not in source.variables or source[name].shape != expected_shape:
                raise ValueError(f"Component {name} is absent or has the wrong shape")
        try:
            with Dataset(partial, "w", format="NETCDF4") as output:
                output.createDimension("time", None)
                output.createDimension("y", ny)
                output.createDimension("x", nx)
                output.createDimension("nv4", 4)
                output.setncatts(
                    {
                        "Conventions": "CF-1.8",
                        "title": "Seven-field hourly NWM LDASIN forcing (precipitation pending)",
                        "forcing_source": thermo_product,
                        "forcing_source_id": SOURCE_IDS[thermo_product],
                        "fallback_used": "yes" if fallback_used else "no",
                        "valid_time": thermo_time,
                        "thermodynamic_component": str(thermodynamic_path),
                        "radiation_wind_component": str(radiation_wind_path),
                        "target_grid": str(target_grid_path),
                        "precipitation_status": "not_present; must be added before model ingestion",
                        "history": f"{datetime.now(UTC).isoformat()} assembled by hydro_ops",
                    }
                )
                time = output.createVariable("time", "f8", ("time",))
                time.setncatts(
                    {
                        "standard_name": "time",
                        "long_name": "Time",
                        "units": "minutes since 1970-01-01 00:00:00 UTC",
                        "calendar": "standard",
                        "axis": "T",
                    }
                )
                time[:] = date2num(datetime.fromisoformat(thermo_time), time.units, time.calendar)
                coordinate_variables = {}
                for name, dimensions in (
                    ("lon", ("y", "x")),
                    ("lat", ("y", "x")),
                    ("lon_bnds", ("y", "x", "nv4")),
                    ("lat_bnds", ("y", "x", "nv4")),
                ):
                    original = grid[name]
                    chunks = (
                        (min(rows_per_chunk, ny), min(288, nx), 4)
                        if len(dimensions) == 3
                        else (min(rows_per_chunk, ny), min(288, nx))
                    )
                    variable = output.createVariable(
                        name, "f8", dimensions, zlib=True, complevel=2,
                        shuffle=True, chunksizes=chunks,
                    )
                    variable.setncatts(
                        {attribute: original.getncattr(attribute) for attribute in original.ncattrs()}
                    )
                    coordinate_variables[name] = variable
                chunks = (1, min(rows_per_chunk, ny), min(288, nx))
                forcing_variables = {}
                for name, (standard_name, long_name, units) in VARIABLES.items():
                    variable = output.createVariable(
                        name, "f4", ("time", "y", "x"), fill_value=np.float32(-9.99e8),
                        zlib=True, complevel=2, shuffle=True, chunksizes=chunks,
                    )
                    variable.setncatts(
                        {
                            "standard_name": standard_name,
                            "long_name": long_name,
                            "units": units,
                            "coordinates": "lat lon",
                            "missing_value": np.float32(-9.99e8),
                        }
                    )
                    forcing_variables[name] = variable
                source_id = output.createVariable(
                    "forcing_source_id", "u1", ("time", "y", "x"),
                    zlib=True, complevel=2, shuffle=True, chunksizes=chunks,
                )
                source_id.setncatts(
                    {"flag_values": np.array([0, 1, 2], dtype=np.uint8),
                     "flag_meanings": "missing nldas2 hrrr"}
                )
                qc = output.createVariable(
                    "forcing_qc_flags", "u4", ("time", "y", "x"),
                    zlib=True, complevel=2, shuffle=True, chunksizes=chunks,
                )
                qc.long_name = "combined QC; thermodynamic low 16 bits, radiation/wind high 16 bits"
                for start in range(0, ny, rows_per_chunk):
                    stop = min(start + rows_per_chunk, ny)
                    for name, variable in coordinate_variables.items():
                        variable[start:stop] = grid[name][start:stop]
                    active = np.asarray(grid["active_domain"][start:stop]) == 1
                    all_valid = active.copy()
                    for name, variable in forcing_variables.items():
                        source = thermo if name in THERMODYNAMIC_VARIABLES else radiation_wind
                        values = source[name][0, start:stop]
                        all_valid &= ~np.ma.getmaskarray(values) & np.isfinite(values.filled(np.nan))
                        variable[0, start:stop] = np.ma.masked_where(~active, values)
                    thermo_qc = np.asarray(thermo["thermodynamic_qc_flags"][0, start:stop], dtype=np.uint32)
                    radiation_qc = np.asarray(
                        radiation_wind["radiation_wind_qc_flags"][0, start:stop], dtype=np.uint32
                    )
                    combined = thermo_qc | (radiation_qc << 16)
                    combined[active & ~all_valid] |= np.uint32(1 << 31)
                    qc[0, start:stop] = combined
                    source_id[0, start:stop] = np.where(
                        all_valid, SOURCE_IDS[thermo_product], 0
                    ).astype(np.uint8)
            partial.replace(output_path)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
    return output_path


def add_precipitation_to_ldasin(
    seven_field_path: Path,
    precipitation_path: Path,
    output_path: Path,
    *,
    force: bool = False,
    rows_per_chunk: int = 120,
) -> Path:
    """Publish a complete eight-field LDASIN by atomically adding processed precipitation."""
    if output_path.exists() and not force:
        raise FileExistsError(f"Output exists; use --force to replace it: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial = output_path.with_name(f"{output_path.name}.part")
    partial.unlink(missing_ok=True)
    try:
        shutil.copyfile(seven_field_path, partial)
        with Dataset(partial, "a") as output, Dataset(precipitation_path) as precipitation:
            if output.getncattr("valid_time") != precipitation.getncattr("valid_time"):
                raise ValueError("Precipitation and seven-field valid times differ")
            ny, nx = len(output.dimensions["y"]), len(output.dimensions["x"])
            if precipitation["RAINRATE"].shape != (1, ny, nx):
                raise ValueError("Precipitation has the wrong target-grid shape")
            chunks = (1, min(rows_per_chunk, ny), min(288, nx))
            definitions = {
                "RAINRATE": ("f4", np.float32(-9.99e8)),
                "precip_source_id": ("u1", None),
                "precip_confidence": ("f4", None),
                "precip_qc_flags": ("u2", None),
            }
            variables = {}
            for name, (dtype, fill_value) in definitions.items():
                keyword = {} if fill_value is None else {"fill_value": fill_value}
                variable = output.createVariable(
                    name, dtype, ("time", "y", "x"), zlib=True, complevel=2,
                    shuffle=True, chunksizes=chunks, **keyword,
                )
                source = precipitation[name]
                variable.setncatts(
                    {attribute: source.getncattr(attribute) for attribute in source.ncattrs()}
                )
                variables[name] = variable
            for start in range(0, ny, rows_per_chunk):
                stop = min(start + rows_per_chunk, ny)
                for name, variable in variables.items():
                    variable[0, start:stop] = precipitation[name][0, start:stop]
            output.setncattr("title", "Complete hourly NWM LDASIN forcing")
            output.setncattr("precipitation_status", "present")
            output.setncattr("precipitation_component", str(precipitation_path))
            output.setncattr(
                "history",
                f"{datetime.now(UTC).isoformat()} precipitation added; "
                + output.getncattr("history"),
            )
        partial.replace(output_path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return output_path
