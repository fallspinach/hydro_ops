from __future__ import annotations

from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.forcing.inventory import inspect_forcing_file


def write_nldas(path: Path, *, temperature_units: str = "K", offset: float = 0.0) -> None:
    with Dataset(path, "w") as data:
        data.createDimension("time", 1)
        data.createDimension("lat", 2)
        data.createDimension("lon", 3)
        time = data.createVariable("time", "f8", ("time",))
        time.units = "hours since 1979-01-01 00:00:00"
        time[:] = [1.0]
        data.createVariable("lat", "f8", ("lat",))[:] = [30.0 + offset, 31.0]
        data.createVariable("lon", "f8", ("lon",))[:] = [-100.0, -99.0, -98.0]
        units = {
            "Tair": temperature_units,
            "Qair": "kg kg-1",
            "PSurf": "Pa",
            "SWdown": "W m-2",
            "LWdown": "W m-2",
            "Wind_E": "m s-1",
            "Wind_N": "m s-1",
            "Rainf": "kg m-2",
        }
        for name, unit in units.items():
            variable = data.createVariable(name, "f4", ("time", "lat", "lon"))
            variable.units = unit
            variable[:] = np.ones((1, 2, 3))


def test_inventory_validates_and_fingerprints_grid(tmp_path: Path) -> None:
    first = tmp_path / "first.nc"
    second = tmp_path / "second.nc"
    changed = tmp_path / "changed.nc"
    write_nldas(first)
    write_nldas(second)
    write_nldas(changed, offset=0.1)
    first_inventory = inspect_forcing_file(first, "nldas2")
    second_inventory = inspect_forcing_file(second, "nldas2")
    changed_inventory = inspect_forcing_file(changed, "nldas2")
    assert first_inventory.valid
    assert first_inventory.valid_time == "1979-01-01T01:00:00"
    assert first_inventory.grid_fingerprint == second_inventory.grid_fingerprint
    assert first_inventory.grid_fingerprint != changed_inventory.grid_fingerprint


def test_inventory_reports_unit_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "bad.nc"
    write_nldas(path, temperature_units="degC")
    inventory = inspect_forcing_file(path, "nldas2")
    assert not inventory.valid
    assert "unexpected units for Tair" in inventory.issues[0]
