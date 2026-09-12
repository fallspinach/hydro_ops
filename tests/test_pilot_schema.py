from datetime import date

import numpy as np
import pytest
from netCDF4 import Dataset

from hydro_ops.forcing.daily_archive import create_daily_archive
from hydro_ops.forcing.pilot_schema import ensure_precipitation_timing


def test_mixed_reconciliation_hours_normalize_without_changing_rain(tmp_path):
    paths = [tmp_path / f"hour{h}.nc" for h in range(2)]
    for h, path in enumerate(paths):
        with Dataset(path, "w") as d:
            for name, size in (("time", 1), ("y", 1), ("x", 2)):
                d.createDimension(name, size)
            t = d.createVariable("time", "f8", ("time",))
            t.units = "hours since 2026-08-26 00:00:00"
            t[:] = h
            d.createVariable("RAINRATE", "f4", ("time", "y", "x"))[:] = .01 + h
            d.createVariable("precip_source_id", "u1", ("time", "y", "x"))[:] = 5
            if h == 0:
                d.createVariable("precip_timing_source_id", "u1", ("time", "y", "x"))[:] = [5, 0]
    output = tmp_path / "daily.nc"
    with pytest.raises(ValueError, match="missing_variables=.*precip_timing_source_id"):
        create_daily_archive(paths, output, date(2026, 8, 26), expected_hours=2)
    assert not output.exists()
    assert not ensure_precipitation_timing(paths[0])
    assert ensure_precipitation_timing(paths[1])
    assert not ensure_precipitation_timing(paths[1])
    create_daily_archive(paths, output, date(2026, 8, 26), expected_hours=2)
    with Dataset(output) as d:
        np.testing.assert_array_equal(d["precip_timing_source_id"][:, 0], [[5, 0], [0, 0]])
        np.testing.assert_allclose(d["RAINRATE"][:, 0], [[.01, .01], [1.01, 1.01]])


def test_unknown_schema_changes_are_still_rejected(tmp_path):
    path = tmp_path / "wrong.nc"
    with Dataset(path, "w") as d:
        d.createDimension("time", 1)
        d.createVariable("precip_source_id", "u1", ("time",))[:] = 5
        d.createVariable("precip_timing_source_id", "f4", ("time",))[:] = 5
    with pytest.raises(ValueError, match="Unexpected timing-source schema"):
        ensure_precipitation_timing(path)
