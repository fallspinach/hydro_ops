from __future__ import annotations

from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.forcing.normalize import open_normalized_forcing


def write_prism(path: Path) -> None:
    with Dataset(path, "w") as data:
        data.createDimension("time", 1)
        data.createDimension("lat", 2)
        data.createDimension("lon", 2)
        time = data.createVariable("time", "f8", ("time",))
        time.units = "days since 2020-01-01 12:00:00"
        time[:] = [0]
        data.createVariable("lat", "f8", ("lat",))[:] = [30.0, 31.0]
        data.createVariable("lon", "f8", ("lon",))[:] = [-100.0, -99.0]
        temperature = data.createVariable("tmin", "f4", ("time", "lat", "lon"))
        temperature.units = "degC"
        temperature[:] = np.array([[[0.0, 1.0], [2.0, 3.0]]])


def write_hrrr(path: Path) -> None:
    with Dataset(path, "w") as data:
        data.createDimension("time", 1)
        data.createDimension("y", 1)
        data.createDimension("x", 2)
        time = data.createVariable("time", "f8", ("time",))
        time.units = "seconds since 1970-01-01 00:00:00"
        time[:] = [0]
        data.createVariable("y", "f8", ("y",))[:] = [0.0]
        data.createVariable("x", "f8", ("x",))[:] = [0.0, 3000.0]
        data.createVariable("latitude", "f8", ("y", "x"))[:] = [[40.0, 40.0]]
        data.createVariable("longitude", "f8", ("y", "x"))[:] = [[250.0, 251.0]]
        variables = {
            "TMP_2maboveground": "K",
            "SPFH_2maboveground": "kg/kg",
            "PRES_surface": "Pa",
            "DSWRF_surface": "W/m^2",
            "DLWRF_surface": "W/m^2",
            "UGRD_10maboveground": "m/s",
            "VGRD_10maboveground": "m/s",
            "APCP_surface": "kg/m^2",
        }
        for name, units in variables.items():
            variable = data.createVariable(name, "f4", ("time", "y", "x"))
            variable.units = units
            variable[:] = np.ones((1, 1, 2))


def test_normalize_prism_temperature_to_kelvin(tmp_path: Path) -> None:
    path = tmp_path / "prism.nc"
    write_prism(path)
    with open_normalized_forcing(path, "prism_tmin") as data:
        assert list(data.data_vars) == ["daily_minimum_temperature"]
        assert data.daily_minimum_temperature.attrs["units"] == "K"
        np.testing.assert_allclose(data.daily_minimum_temperature[0, 0, 0], 273.15)


def test_normalize_hrrr_names_longitude_and_wind_orientation(tmp_path: Path) -> None:
    path = tmp_path / "hrrr.nc"
    write_hrrr(path)
    with open_normalized_forcing(path, "hrrr") as data:
        assert set(data.data_vars) == {
            "air_temperature",
            "specific_humidity",
            "surface_pressure",
            "downward_shortwave",
            "downward_longwave",
            "wind_u",
            "wind_v",
            "precipitation_depth",
        }
        assert data.attrs["wind_orientation"] == "grid_relative"
        np.testing.assert_allclose(data.longitude, [[-110.0, -109.0]])
        assert len(data.attrs["source_grid_fingerprint"]) == 64
