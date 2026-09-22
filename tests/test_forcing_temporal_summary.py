from datetime import date, datetime, timedelta

import numpy as np
import pytest
from netCDF4 import Dataset, date2num

from hydro_ops.forcing.temporal_summary import TIME_UNITS, numeric_time, periods, summarize

REDUCERS = {"T2D": "mean", "RAINRATE": "integral"}
NAMES = {"RAINRATE": "RAIN_DEPTH"}
UNITS = {"RAINRATE": "kg m-2"}


def chunk(root, day, value=280.0, offset=0):
    path = root / f"{day:%Y/%m/%Y%m%d}.LDASIN_DOMAIN1"
    path.parent.mkdir(parents=True, exist_ok=True)
    with Dataset(path, "w") as d:
        for name, size in [("time", 24), ("y", 2), ("x", 2)]:
            d.createDimension(name, size)
        t = d.createVariable("time", "f8", ("time",))
        t.units = TIME_UNITS
        t[:] = date2num(
            [datetime.combine(day, datetime.min.time()) + timedelta(hours=i) for i in range(24)],
            TIME_UNITS,
        )
        for name in ("lat", "lon"):
            d.createVariable(name, "f4", ("y", "x"))[:] = [[1, 2], [3, 4]]
        for name in REDUCERS:
            v = d.createVariable(name, "f4", ("time", "y", "x"), fill_value=-9999)
            v.units = "K" if name == "T2D" else "kg m-2 s-1"
            values = np.full((24, 2, 2), value if name == "T2D" else 1 / 3600, "f4")
            if name == "T2D" and offset:
                values[:] = (offset + np.arange(24))[:, None, None]
            values[:, 1, 1] = -9999
            v[:] = values
    return path


def run(root, out, start, stop, **kwargs):
    return summarize(root, out, start, stop, REDUCERS, NAMES, UNITS, block_rows=1, **kwargs)


def test_day_endpoints_and_missing_propagation(tmp_path):
    root = tmp_path / "raw"
    day = date(2025, 12, 31)
    p = chunk(root, day, offset=100)
    chunk(root, day + timedelta(days=1), offset=124)
    with Dataset(p, "r+") as d:
        d["T2D"][0, 0, 0] = 9999  # Excluded day-start value.
        d["T2D"][12, 1, 0] = -9999
    out = tmp_path / "daily.nc"
    run(root, out, day, day + timedelta(days=1))
    with Dataset(out) as d:
        np.testing.assert_allclose(d["T2D"][0, 0, 0], 112.5)
        np.testing.assert_allclose(d["RAIN_DEPTH"][0, 0, 0], 24)
        assert np.ma.getmaskarray(d["T2D"][0])[1, 0]
        assert np.ma.getmaskarray(d["RAIN_DEPTH"][0])[1, 1]
        assert d.aggregation_sample_count == 24
        assert numeric_time(d["time_bounds"])[0, 1] - numeric_time(d["time_bounds"])[0, 0] == 24
        np.testing.assert_array_equal(d["lat"][:], [[1, 2], [3, 4]])


def test_leap_month_hourly_equals_daily_route(tmp_path):
    root = tmp_path / "raw"
    daily = tmp_path / "daily"
    start = date(2024, 2, 1)
    stop = date(2024, 3, 1)
    for i in range(30):
        chunk(root, start + timedelta(days=i), value=270 + i)
    for day, end in periods(start, stop - timedelta(days=1), "daily"):
        run(root, daily / f"{day:%Y/%m/%Y%m%d}.FORCING_DAILY.nc", day, end)
    direct = tmp_path / "direct.nc"
    reused = tmp_path / "reused.nc"
    run(root, direct, start, stop)
    run(daily, reused, start, stop, from_daily=True)
    with Dataset(direct) as a, Dataset(reused) as b:
        for name in ("T2D", "RAIN_DEPTH"):
            np.testing.assert_allclose(a[name][:], b[name][:], rtol=1e-6)
        assert a.aggregation_sample_count == 696
        assert b.temporal_resolution == "P1M"
        np.testing.assert_allclose(b["RAIN_DEPTH"][0, 0, 0], 696, rtol=1e-6)


def test_missing_boundary_timestamp_fails(tmp_path):
    start = date(2026, 1, 1)
    stop = start + timedelta(days=1)
    chunk(tmp_path, start)
    p = chunk(tmp_path, stop)
    with Dataset(p, "r+") as d:
        d["time"][0] += 0.5
    with pytest.raises(ValueError, match="timestamps"):
        run(tmp_path, tmp_path / "out.nc", start, stop)
    assert not (tmp_path / "out.nc").exists()


def test_grid_mismatch_rejected(tmp_path):
    start = date(2026, 1, 1)
    stop = start + timedelta(days=1)
    chunk(tmp_path, start)
    p = chunk(tmp_path, stop)
    with Dataset(p, "r+") as d:
        d["lat"][0, 0] += 1
    with pytest.raises(AssertionError):
        run(tmp_path, tmp_path / "out.nc", start, stop)


def test_repeat_and_stale_sources(tmp_path):
    start = date(2026, 1, 1)
    stop = start + timedelta(days=1)
    p = chunk(tmp_path, start)
    chunk(tmp_path, stop)
    out = tmp_path / "out.nc"
    run(tmp_path, out, start, stop)
    assert run(tmp_path, out, start, stop, skip_existing=True)["status"] == "unchanged"
    with Dataset(p, "r+") as d:
        d["T2D"][1, 0, 0] += 1
    with pytest.raises(ValueError, match="Stale summary"):
        run(tmp_path, out, start, stop, skip_existing=True)
    assert run(tmp_path, out, start, stop, overwrite=True)["status"] == "published"


def test_partial_month_rejected():
    with pytest.raises(ValueError, match="Monthly requests"):
        list(periods(date(2026, 2, 2), date(2026, 2, 28), "monthly"))
    assert len(list(periods(date(2025, 12, 1), date(2026, 2, 28), "monthly"))) == 3


def test_production_slash_precipitation_units(tmp_path):
    start = date(2026, 1, 1)
    stop = start + timedelta(days=1)
    for day in (start, stop):
        path = chunk(tmp_path, day)
        with Dataset(path, "r+") as d:
            d["RAINRATE"].units = "kg/m^2/s"
    run(tmp_path, tmp_path / "out.nc", start, stop)
    with Dataset(tmp_path / "out.nc") as d:
        np.testing.assert_allclose(d["RAIN_DEPTH"][0, 0, 0], 24)


def test_tampered_daily_bounds_rejected(tmp_path):
    # Reject an incompatible daily product before using it as a monthly input.
    root = tmp_path / "daily"
    for i in range(28):
        day = date(2026, 2, 1) + timedelta(days=i)
        p = root / f"{day:%Y/%m/%Y%m%d}.FORCING_DAILY.nc"
        p.parent.mkdir(parents=True, exist_ok=True)
        with Dataset(p, "w") as d:
            d.createDimension("y", 1)
            d.createDimension("x", 1)
            d.createDimension("time", 1)
            t = d.createVariable("time", "f8", ("time",))
            t.units = TIME_UNITS
            t[:] = 0
    with pytest.raises(ValueError, match="verified compatible"):
        run(root, tmp_path / "out.nc", date(2026, 2, 1), date(2026, 3, 1), from_daily=True)
