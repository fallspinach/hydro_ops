from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import xarray as xr
from netCDF4 import Dataset

from hydro_ops.forcing.precipitation import PrecipitationQC
from hydro_ops.forcing.precipitation_day import (
    process_precipitation_day,
    reconcile_stage4_six_hour_block,
)


def _write_precip_hour(path: Path, depth: np.ndarray, source_id: int = 5) -> None:
    with Dataset(path, "w", format="NETCDF4") as data:
        data.createDimension("time", 1)
        data.createDimension("y", depth.shape[0])
        data.createDimension("x", depth.shape[1])
        data.createVariable("RAINRATE", "f4", ("time", "y", "x"))[:] = depth / 3600
        data.createVariable("precip_source_id", "u1", ("time", "y", "x"))[:] = source_id
        data.createVariable("precip_confidence", "f4", ("time", "y", "x"))[:] = 0.4
        data.createVariable("precip_qc_flags", "u2", ("time", "y", "x"))[:] = np.uint16(
            PrecipitationQC.CNRFC_HOURLY_STAGE4_REJECTED
        )


def test_six_hour_reconciliation_conserves_total_and_records_timing(tmp_path: Path) -> None:
    paths = [tmp_path / f"hour{hour}.nc" for hour in range(6)]
    for hour, path in enumerate(paths, start=1):
        _write_precip_hour(path, np.array([[float(hour), 0.0, 4.0]], dtype=np.float32))
    reconcile_stage4_six_hour_block(
        paths,
        np.array([[42.0, 12.0, 99.0]], dtype=np.float32),
        np.array([[True, True, False]]),
        constraint_path=tmp_path / "stage4.06h.nc",
        constraint_end=datetime(2026, 1, 1, 6, tzinfo=UTC),
    )
    depths = []
    for path in paths:
        with Dataset(path) as data:
            depths.append(np.asarray(data["RAINRATE"][0]) * 3600)
            np.testing.assert_array_equal(data["precip_source_id"][0, 0, :2], [7, 7])
            np.testing.assert_array_equal(data["precip_timing_source_id"][0, 0, :2], [5, 5])
            assert data["precip_qc_flags"][0, 0, 0] & int(
                PrecipitationQC.CNRFC_SIX_HOUR_CONSTRAINED
            )
            assert data["precip_qc_flags"][0, 0, 1] & int(
                PrecipitationQC.CNRFC_TIMING_FALLBACK
            )
    total = np.sum(depths, axis=0)
    np.testing.assert_allclose(total[0, :2], [42.0, 12.0], rtol=2e-6)
    np.testing.assert_allclose(total[0, 2], 24.0, rtol=2e-6)


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
