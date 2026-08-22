"""Validate forcing NetCDF structure and fingerprint native grids."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, num2date

PRODUCT_VARIABLES = {
    "nldas2": {
        "Tair": "K",
        "Qair": "kg kg-1",
        "PSurf": "Pa",
        "SWdown": "W m-2",
        "LWdown": "W m-2",
        "Wind_E": "m s-1",
        "Wind_N": "m s-1",
        "Rainf": "kg m-2",
    },
    "hrrr": {
        "TMP_2maboveground": "K",
        "SPFH_2maboveground": "kg/kg",
        "PRES_surface": "Pa",
        "DSWRF_surface": "W/m^2",
        "DLWRF_surface": "W/m^2",
        "UGRD_10maboveground": "m/s",
        "VGRD_10maboveground": "m/s",
        "APCP_surface": "kg/m^2",
    },
    "prism_tmin": {"tmin": "degC"},
    "prism_tmax": {"tmax": "degC"},
    "prism_tmean": {"tmean": "degC"},
    "prism_ppt": {"ppt": "mm"},
    "mrms_pass1": {"MultiSensorQPE01HPass1_0mabovemeansealevel": "mm"},
    "mrms_pass2": {"MultiSensorQPE01HPass2_0mabovemeansealevel": "mm"},
    "mrms_quality": {"RadarAccumulationQualityIndex01H_0mabovemeansealevel": "non-dim"},
    "stage4_archive": {"APCP_surface": "kg/m^2"},
    "stage4_realtime": {"APCP_surface": "kg/m^2"},
}

GRID_VARIABLES = {
    "nldas2": ("lat", "lon"),
    "hrrr": ("y", "x", "latitude", "longitude"),
    "prism_tmin": ("lat", "lon"),
    "prism_tmax": ("lat", "lon"),
    "prism_tmean": ("lat", "lon"),
    "prism_ppt": ("lat", "lon"),
    "mrms_pass1": ("latitude", "longitude"),
    "mrms_pass2": ("latitude", "longitude"),
    "mrms_quality": ("latitude", "longitude"),
    "stage4_archive": ("y", "x", "latitude", "longitude"),
    "stage4_realtime": ("y", "x", "latitude", "longitude"),
}


@dataclass(frozen=True)
class FileInventory:
    path: str
    product: str
    valid_time: str | None
    grid_fingerprint: str
    dimensions: dict[str, int]
    variables: tuple[str, ...]
    issues: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.issues

    def to_json(self) -> str:
        values = asdict(self)
        values["valid"] = self.valid
        return json.dumps(values, sort_keys=True)


def _hash_variable(digest, variable, *, rows: int = 128) -> None:
    digest.update(variable.name.encode())
    digest.update(str(variable.dtype).encode())
    digest.update(repr(variable.shape).encode())
    if variable.ndim == 0:
        digest.update(np.ascontiguousarray(variable[...]).tobytes())
        return
    for start in range(0, variable.shape[0], rows):
        values = np.ma.filled(variable[start : start + rows, ...], np.nan)
        digest.update(np.ascontiguousarray(values).tobytes())


def grid_fingerprint(dataset: Dataset, product: str) -> str:
    """Hash native coordinate values and dimensions without loading a full grid at once."""
    digest = hashlib.sha256()
    for name, dimension in sorted(dataset.dimensions.items()):
        if name != "time":
            digest.update(f"{name}:{dimension.size}".encode())
    for name in GRID_VARIABLES[product]:
        if name not in dataset.variables:
            digest.update(f"missing:{name}".encode())
        else:
            _hash_variable(digest, dataset[name])
    return digest.hexdigest()


def netcdf_grid_fingerprint(path: Path, variables: tuple[str, ...]) -> str:
    """Hash selected coordinate/mask arrays for a static NetCDF grid definition."""
    digest = hashlib.sha256()
    with Dataset(path) as dataset:
        for name in variables:
            if name not in dataset.variables:
                raise ValueError(f"Grid file {path} is missing {name}")
            _hash_variable(digest, dataset[name])
    return digest.hexdigest()


def _valid_time(dataset: Dataset) -> str | None:
    if "time" not in dataset.variables or dataset["time"].size != 1:
        return None
    variable = dataset["time"]
    units = getattr(variable, "units", None)
    if not units:
        return None
    calendar = getattr(variable, "calendar", "standard")
    value = num2date(variable[0], units, calendar=calendar, only_use_cftime_datetimes=False)
    return value.isoformat()


def inspect_forcing_file(path: Path, product: str) -> FileInventory:
    """Validate required variables and units and return a reproducible grid identity."""
    if product not in PRODUCT_VARIABLES:
        raise ValueError(f"Unknown product {product!r}")
    issues: list[str] = []
    with Dataset(path) as dataset:
        for name, expected_units in PRODUCT_VARIABLES[product].items():
            if name not in dataset.variables:
                issues.append(f"missing variable {name}")
                continue
            actual_units = getattr(dataset[name], "units", None)
            if actual_units != expected_units:
                issues.append(
                    f"unexpected units for {name}: {actual_units!r}; expected {expected_units!r}"
                )
            if "time" not in dataset[name].dimensions:
                issues.append(f"variable {name} has no time dimension")
        if "time" not in dataset.dimensions or dataset.dimensions["time"].size != 1:
            issues.append("expected exactly one time value")
        for name in GRID_VARIABLES[product]:
            if name not in dataset.variables:
                issues.append(f"missing grid variable {name}")
        dimensions = {name: dimension.size for name, dimension in dataset.dimensions.items()}
        variables = tuple(sorted(dataset.variables))
        fingerprint = grid_fingerprint(dataset, product)
        try:
            valid_time = _valid_time(dataset)
        except (OverflowError, TypeError, ValueError) as error:
            valid_time = None
            issues.append(f"invalid time coordinate: {error}")
    return FileInventory(
        str(path), product, valid_time, fingerprint, dimensions, variables, tuple(issues)
    )
