from datetime import date, timedelta
from pathlib import Path

import numpy as np
from netCDF4 import Dataset
from test_forcing_temporal_summary import chunk

from hydro_ops.forcing.model_interval import load_forcing_reducers, reduce_model_interval_forcing
from hydro_ops.forcing.temporal_summary import periods, summarize


def test_default_daily_and_monthly(tmp_path):
    reducers, names, units = load_forcing_reducers(
        Path(__file__).resolve().parents[1] / "config/forcing_daily_reducers.toml"
    )
    start, stop = date(2024, 2, 1), date(2024, 3, 1)
    raw, daily = tmp_path / "raw", tmp_path / "daily"
    paths = []
    for i in range(30):
        p = chunk(raw, start + timedelta(days=i), offset=270 + 24 * i)
        paths.append(p)
        with Dataset(p, "r+") as d:
            for name in ("Q2D", "PSFC", "SWDOWN", "LWDOWN", "U2D", "V2D"):
                v = d.createVariable(name, "f4", ("time", "y", "x"), fill_value=-9999)
                v.units = "m s-1" if name in ("U2D", "V2D") else "1"
                v[:] = 0
                if name == "U2D":
                    v[:] = np.where(np.arange(24) % 2, 3, -3)[:, None, None]
                if name == "V2D":
                    v[:] = 4
                    v[:, 1, 1] = -9999
    for day, end in periods(start, stop - timedelta(days=1), "daily"):
        out = daily / f"{day:%Y/%m/%Y%m%d}.FORCING_DAILY.nc"
        summarize(raw, out, day, end, reducers, names, units, block_rows=1)
    first = daily / "2024/02/20240201.FORCING_DAILY.nc"
    with Dataset(first) as d:
        assert "RAIN_DEPTH" not in d.variables and "U2D" not in d.variables
        assert d["T2D_MIN"][0, 0, 0] == 271
        assert d["T2D_MAX"][0, 0, 0] == 294
        assert d["WIND_SPEED"][0, 0, 0] == 5
        assert np.ma.getmaskarray(d["WIND_SPEED"][:])[0, 1, 1]
        np.testing.assert_allclose(d["RAINRATE"][0, 0, 0], 1 / 3600)
        assert d["WIND_SPEED"].standard_name == "wind_speed"
        assert d["T2D_MAX"].cell_methods == "time: maximum"
    legacy = reduce_model_interval_forcing(paths[:2], start, reducers)
    assert legacy.WIND_SPEED.values[0, 0, 0] == 5
    assert legacy.T2D_MIN.values[0, 0, 0] == 271
    for root, filename, reuse in [(raw, "direct.nc", False), (daily, "reused.nc", True)]:
        summarize(
            root,
            tmp_path / filename,
            start,
            stop,
            reducers,
            names,
            units,
            from_daily=reuse,
            block_rows=1,
        )
    with Dataset(tmp_path / "direct.nc") as a, Dataset(tmp_path / "reused.nc") as b:
        for name in reducers:
            np.testing.assert_allclose(a[name][:], b[name][:], rtol=1e-6)
        assert b["T2D_MIN"][0, 0, 0] == 607  # mean(271 + 24 * day), not min=271
        assert b["T2D_MAX"][0, 0, 0] == 630  # mean(294 + 24 * day), not max=966
        assert b["T2D"][0, 0, 0] == 618.5
        assert b["T2D_MIN"].cell_methods == "time: minimum within days time: mean over days"
        assert b["T2D_MAX"].cell_methods == "time: maximum within days time: mean over days"
        assert np.ma.getmaskarray(b["T2D_MIN"][:])[0, 1, 1]
