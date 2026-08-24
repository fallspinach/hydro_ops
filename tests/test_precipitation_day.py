from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import xarray as xr
from netCDF4 import Dataset

from hydro_ops.forcing.precipitation_day import process_precipitation_day


def test_daily_batch_applies_one_remap_and_writes_each_hour(
    tmp_path: Path, monkeypatch
) -> None:
    start = datetime(2026, 1, 1, 0, tzinfo=UTC)
    valid_times = [start, start + timedelta(hours=1)]
    source = tmp_path / "nldas.nc"
    xr.Dataset(
        {
            "Rainf": (
                ("time", "lat", "lon"),
                np.array([[[1, 2, 3], [4, 5, 6]], [[2, 3, 4], [5, 6, 7]]], dtype=np.float32),
            )
        },
        coords={"time": [value.replace(tzinfo=None) for value in valid_times]},
    ).to_netcdf(source)
    target = tmp_path / "target.nc"
    with Dataset(target, "w") as data:
        data.createDimension("y", 2)
        data.createDimension("x", 3)
        data.createVariable("active_domain", "i1", ("y", "x"))[:] = 1
        data.createVariable("lat", "f4", ("y", "x"))[:] = 40
        data.createVariable("lon", "f4", ("y", "x"))[:] = -110
    remap_grid = tmp_path / "target.scrip.nc"
    remap_grid.touch()
    weights = tmp_path / "weights.nc"
    weights.touch()
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        shutil.copyfile(command[-2], command[-1])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("hydro_ops.forcing.precipitation_day.shutil.which", lambda _: "cdo")
    monkeypatch.setattr("hydro_ops.forcing.precipitation_day.subprocess.run", fake_run)
    outputs = process_precipitation_day(
        valid_times,
        [{"nldas2": source}, {"nldas2": source}],
        [None, None],
        {"nldas2": weights},
        target,
        remap_grid,
        tmp_path / "output",
        validate_weights=False,
    )
    assert len(calls) == 1
    assert len(outputs) == 2
    with Dataset(outputs[1]) as data:
        np.testing.assert_allclose(data["RAINRATE"][0], np.array([[2, 3, 4], [5, 6, 7]]) / 3600)
        assert data.getncattr("precipitation_remap_mode") == "daily_batch"
