from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from netCDF4 import Dataset, date2num

from hydro_ops.forcing.stream_validation import (
    REQUIRED_FIELDS,
    validate_daily_forcing,
    validate_hourly_forcing_day,
)


def _daily(path: Path, *, negative_rain: bool = False) -> None:
    units = "hours since 1970-01-01 00:00:00 UTC"
    times = [datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=index) for index in range(24)]
    with Dataset(path, "w") as data:
        data.createDimension("time", 24)
        data.createDimension("y", 2)
        data.createDimension("x", 3)
        time = data.createVariable("time", "f8", ("time",))
        time.units = units
        time[:] = date2num(times, units)
        values = {
            "T2D": 280.0,
            "Q2D": 0.005,
            "PSFC": 90_000.0,
            "U2D": 1.0,
            "V2D": 2.0,
            "SWDOWN": 100.0,
            "LWDOWN": 300.0,
            "RAINRATE": -0.1 if negative_rain else 0.001,
        }
        for name in REQUIRED_FIELDS:
            variable = data.createVariable(name, "f4", ("time", "y", "x"), fill_value=-9999.0)
            variable[:] = values[name]
        data.createVariable("forcing_source_id", "u1", ("time", "y", "x"))[:] = 1
        data.createVariable("precip_source_id", "u1", ("time", "y", "x"))[:] = 2
        data.setncattr("prism_precipitation_revision", "stable")
        data.setncattr("prism_reconciliation_accepted", "true")
    path.with_suffix(path.suffix + ".manifest.json").write_text(
        json.dumps({"verified": True})
    )


def test_daily_stream_validation_accepts_complete_file(tmp_path: Path) -> None:
    path = tmp_path / "day.nc"
    _daily(path)

    report = validate_daily_forcing(path, expected_revision="stable", expected_grid=(2, 3))

    assert report["accepted"]
    assert report["metrics"]["forcing_source_counts"] == {1: 144}
    assert report["metrics"]["precipitation_source_counts"] == {2: 144}


def test_daily_stream_validation_rejects_negative_precipitation(tmp_path: Path) -> None:
    path = tmp_path / "day.nc"
    _daily(path, negative_rain=True)

    report = validate_daily_forcing(path, expected_revision="stable", expected_grid=(2, 3))

    assert not report["accepted"]
    assert any("RAINRATE range" in issue for issue in report["issues"])


def test_hourly_stream_validation_accepts_complete_day(tmp_path: Path) -> None:
    day = date(2026, 1, 1)
    for hour in range(24):
        valid = datetime(2026, 1, 1, hour, tzinfo=UTC)
        path = tmp_path / valid.strftime("%Y/%m/%d/%Y%m%d%H.LDASIN_DOMAIN1")
        path.parent.mkdir(parents=True, exist_ok=True)
        with Dataset(path, "w") as data:
            data.createDimension("time", 1)
            data.createDimension("y", 2)
            data.createDimension("x", 3)
            time = data.createVariable("time", "f8", ("time",))
            time.units = "hours since 1970-01-01 00:00:00 UTC"
            time[:] = date2num([valid], time.units)
            for name, value in zip(
                REQUIRED_FIELDS,
                (280.0, 0.005, 90_000.0, 1.0, 2.0, 100.0, 300.0, 0.001),
                strict=True,
            ):
                data.createVariable(name, "f4", ("time", "y", "x"))[:] = value
            data.createVariable("forcing_source_id", "u1", ("time", "y", "x"))[:] = 1
            data.createVariable("precip_source_id", "u1", ("time", "y", "x"))[:] = 2
            data.setncattr("valid_time", valid.replace(tzinfo=None).isoformat())
            data.setncattr("precipitation_status", "present")
        path.with_suffix(f"{path.suffix}.manifest.json").write_text("{}")

    report = validate_hourly_forcing_day(tmp_path, day, expected_grid=(2, 3))

    assert report["accepted"]
    assert report["metrics"]["T2D"]["finite_count"] == 144
