"""Normalize downloaded products into a common, source-grid forcing schema."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import xarray as xr

from hydro_ops.forcing.inventory import inspect_forcing_file

CANONICAL_UNITS = {
    "air_temperature": "K",
    "specific_humidity": "kg kg-1",
    "surface_pressure": "Pa",
    "downward_shortwave": "W m-2",
    "downward_longwave": "W m-2",
    "wind_u": "m s-1",
    "wind_v": "m s-1",
    "precipitation_depth": "kg m-2",
}

SOURCE_VARIABLES = {
    "nldas2": {
        "Tair": "air_temperature",
        "Qair": "specific_humidity",
        "PSurf": "surface_pressure",
        "SWdown": "downward_shortwave",
        "LWdown": "downward_longwave",
        "Wind_E": "wind_u",
        "Wind_N": "wind_v",
        "Rainf": "precipitation_depth",
    },
    "hrrr": {
        "TMP_2maboveground": "air_temperature",
        "SPFH_2maboveground": "specific_humidity",
        "PRES_surface": "surface_pressure",
        "DSWRF_surface": "downward_shortwave",
        "DLWRF_surface": "downward_longwave",
        "UGRD_10maboveground": "wind_u",
        "VGRD_10maboveground": "wind_v",
        "APCP_surface": "precipitation_depth",
    },
}

PRISM_VARIABLES = {
    "prism_tmin": ("tmin", "daily_minimum_temperature"),
    "prism_tmax": ("tmax", "daily_maximum_temperature"),
    "prism_tmean": ("tmean", "daily_midpoint_temperature"),
    "prism_ppt": ("ppt", "daily_precipitation_depth"),
}


def _normalize_longitude(dataset: xr.Dataset, name: str) -> xr.Dataset:
    longitude = dataset[name]
    if float(longitude.max()) <= 180.0:
        return dataset
    converted = ((longitude + 180.0) % 360.0) - 180.0
    converted.attrs = longitude.attrs
    return dataset.assign({name: converted})


def open_normalized_forcing(
    path: Path, product: str, *, valid_time: datetime | None = None
) -> xr.Dataset:
    """Open one validated source file with canonical names, units, and metadata."""
    inventory = inspect_forcing_file(path, product)
    if not inventory.valid:
        raise ValueError(f"Invalid {product} forcing file {path}: {'; '.join(inventory.issues)}")
    source = xr.open_dataset(path, mask_and_scale=True, decode_times=True)
    selected_time: str | None = inventory.valid_time
    if valid_time is not None and source.sizes.get("time", 0) > 1:
        requested = np.datetime64(valid_time.replace(tzinfo=None), "ns")
        available = np.asarray(source["time"].values).astype("datetime64[ns]")
        matches = np.flatnonzero(available == requested)
        if matches.size != 1:
            source.close()
            raise ValueError(f"{path} has no unique value for {valid_time.isoformat()}")
        source = source.isel(time=[int(matches[0])])
        selected_time = valid_time.replace(tzinfo=None).isoformat()
    if product in SOURCE_VARIABLES:
        mapping = SOURCE_VARIABLES[product]
        dataset = source[list(mapping)].rename(mapping)
        for name, units in CANONICAL_UNITS.items():
            dataset[name].attrs["units"] = units
        coordinate_names = ("lat", "lon") if product == "nldas2" else ("y", "x")
        dataset = dataset.assign_coords({name: source[name] for name in coordinate_names})
        if product == "hrrr":
            dataset = dataset.assign_coords(
                latitude=source["latitude"], longitude=source["longitude"]
            )
            dataset = _normalize_longitude(dataset, "longitude")
            wind_orientation = "grid_relative"
        else:
            wind_orientation = "earth_relative"
        dataset.attrs["wind_orientation"] = wind_orientation
    elif product in PRISM_VARIABLES:
        source_name, target_name = PRISM_VARIABLES[product]
        dataset = source[[source_name]].rename({source_name: target_name})
        if product == "prism_ppt":
            dataset[target_name].attrs["units"] = "mm"
        else:
            dataset[target_name] = dataset[target_name] + 273.15
            dataset[target_name].attrs.update(
                {"units": "K", "conversion": "source degC + 273.15"}
            )
        dataset = dataset.assign_coords(lat=source["lat"], lon=source["lon"])
    else:
        source.close()
        raise ValueError(f"Unknown product {product!r}")
    dataset.attrs.update(
        {
            "source_product": product,
            "source_file": str(path),
            "source_grid_fingerprint": inventory.grid_fingerprint,
            "source_valid_time": selected_time or "unknown",
        }
    )
    return dataset
