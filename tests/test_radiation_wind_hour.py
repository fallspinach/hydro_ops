from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.forcing.radiation_wind_hour import RadiationWindQC, process_radiation_wind_hour


def _write_source(path: Path) -> None:
    with Dataset(path, "w") as data:
        data.createDimension("time", 1)
        data.createDimension("lat", 2)
        data.createDimension("lon", 3)
        time = data.createVariable("time", "f8", ("time",))
        time.units = "hours since 1979-01-01 00:00:00"
        time[:] = [1]
        data.createVariable("lat", "f8", ("lat",))[:] = [30, 31]
        data.createVariable("lon", "f8", ("lon",))[:] = [-100, -99, -98]
        definitions = {
            "Tair": ("K", 290.0),
            "Qair": ("kg kg-1", 0.008),
            "PSurf": ("Pa", 95_000.0),
            "SWdown": ("W m-2", 500.0),
            "LWdown": ("W m-2", 320.0),
            "Wind_E": ("m s-1", 2.0),
            "Wind_N": ("m s-1", 1.0),
            "Rainf": ("kg m-2", 0.0),
        }
        for name, (units, value) in definitions.items():
            variable = data.createVariable(name, "f4", ("time", "lat", "lon"))
            variable.units = units
            variable[:] = value


def _write_target(path: Path) -> None:
    with Dataset(path, "w") as data:
        data.createDimension("y", 2)
        data.createDimension("x", 3)
        data.createVariable("lat", "f4", ("y", "x"))[:] = [[30] * 3, [31] * 3]
        data.createVariable("lon", "f4", ("y", "x"))[:] = [[-100, -99, -98]] * 2
        data.createVariable("active_domain", "i1", ("y", "x"))[:] = [
            [1, 1, 1],
            [1, 0, 1],
        ]


def test_process_radiation_wind_hour_enforces_night_and_preserves_wind(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source.nc"
    target = tmp_path / "target.nc"
    weights = tmp_path / "weights.nc"
    output = tmp_path / "output.nc"
    _write_source(source)
    _write_target(target)
    weights.touch()
    monkeypatch.setattr(shutil, "which", lambda _: "/bin/cdo")

    def fake_run(command, **kwargs):
        shutil.copyfile(command[-2], command[-1])
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    process_radiation_wind_hour(
        source, "nldas2", target, weights, output, validate_weights=False
    )
    with Dataset(output) as data:
        assert data["SWDOWN"][0, 0, 0] == 0.0
        assert data["U2D"][0, 0, 0] == 2.0
        assert data["V2D"][0, 0, 0] == 1.0
        assert data["COSZEN"][0, 0, 0] < 0.0
        assert (
            data["radiation_wind_qc_flags"][0, 0, 0]
            & RadiationWindQC.SHORTWAVE_NIGHT_ZEROED
        )
        assert np.ma.getmaskarray(data["SWDOWN"][:])[0, 1, 1]
        assert data.getncattr("output_wind_orientation") == "earth_relative"
