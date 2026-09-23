import json
from datetime import date

import numpy as np
import pytest
from netCDF4 import Dataset

from hydro_ops.forcing.baseline_schema import FIELDS, SPECS, UnknownDiagnostic
from hydro_ops.forcing.daily_archive import create_daily_archive


@pytest.mark.parametrize("chunk_copy", [False, True])
@pytest.mark.parametrize("modern_first", [False, True])
def test_mixed_legacy_modern(tmp_path, chunk_copy, modern_first):
    paths = [tmp_path / f"{i}.nc" for i in range(2)]
    for i, path in enumerate(paths):
        with Dataset(path, "w") as ds:
            for name, size in [("time", 1), ("y", 2), ("x", 3)]:
                ds.createDimension(name, size)
            t = ds.createVariable("time", "f8", ("time",))
            t.units = "hours since 2003-01-01"
            t[:] = i
            for name in FIELDS | {"precip_source_id"}:
                ds.createVariable(name, "u1" if name == "precip_source_id" else "f4",
                                  ("time", "y", "x"), zlib=True, complevel=2,
                                  chunksizes=(1, 2, 3))[:] = i + 1
            if (i == 0) == modern_first:
                for name in SPECS:
                    virtual = UnknownDiagnostic(ds, name)
                    v = ds.createVariable(name, virtual.dtype, virtual.dimensions,
                                          fill_value=virtual.getncattr("_FillValue"))
                    v.setncatts({k: virtual.getncattr(k) for k in virtual.ncattrs() if k != "_FillValue"})
                    v[:] = 7
    identities = [(p.stat().st_size, p.stat().st_mtime_ns) for p in paths]
    output = tmp_path / "daily.nc"
    create_daily_archive(paths, output, date(2003, 1, 1), expected_hours=2,
                         chunk_copy=chunk_copy)
    with Dataset(output) as ds:
        assert ds.baseline_schema_version == "1"
        for name in SPECS:
            assert np.ma.getmaskarray(ds[name][int(modern_first)]).all()
            assert (ds[name][int(not modern_first)] == 7).all()
        for name in FIELDS:
            np.testing.assert_array_equal(ds[name][:, 0, 0], [1, 2])
    assert identities == [(p.stat().st_size, p.stat().st_mtime_ns) for p in paths]
    if chunk_copy:
        assert json.loads(output.with_suffix(".nc.manifest.json").read_text())["archive_writer"] == "compressed_chunks"
    with Dataset(paths[1], "a") as ds:
        ds["T2D"].units = "wrong"
    with Dataset(paths[0], "a") as ds:
        ds["T2D"].units = "K"
    with pytest.raises(ValueError, match="metadata"):
        create_daily_archive(paths, tmp_path / "invalid.nc", date(2003, 1, 1), expected_hours=2)


@pytest.mark.parametrize("chunk_copy", [False, True])
@pytest.mark.parametrize("units_first", [False, True])
def test_present_diagnostics_missing_attributes_and_window_reassembly(tmp_path, chunk_copy, units_first):
    paths = [tmp_path / f"source-{i}.nc" for i in range(2)]
    for i, path in enumerate(paths):
        with Dataset(path, "w") as ds:
            for name, size in [("time", 1), ("y", 2), ("x", 3)]:
                ds.createDimension(name, size)
            t = ds.createVariable("time", "f8", ("time",))
            t.units = "hours since 2003-01-01"
            t[:] = i
            for name in FIELDS:
                ds.createVariable(name, "f4", ("time", "y", "x"), zlib=True,
                                  complevel=2, chunksizes=(1, 2, 3))[:] = i
            for name in SPECS:
                schema = UnknownDiagnostic(ds, name)
                options = {"zlib": True, "complevel": 2, "chunksizes": (1, 2, 3)} if schema.ndim == 3 else {}
                v = ds.createVariable(name, schema.dtype, schema.dimensions,
                                      fill_value=schema.getncattr("_FillValue"), **options)
                if name == "gfs_forecast_reference_time" or (i == 0) == units_first:
                    v.setncatts({k: schema.getncattr(k) for k in schema.ncattrs() if k != "_FillValue"})
                v[:] = i
    before = [p.read_bytes() for p in paths]
    direct = tmp_path / "direct.nc"
    create_daily_archive(paths, direct, date(2003, 1, 1), expected_hours=2, chunk_copy=chunk_copy)
    windows = [tmp_path / f"window-{i}.nc" for i in range(2)]
    for path, window in zip(paths, windows, strict=True):
        create_daily_archive([path], window, date(2003, 1, 1), expected_hours=1, chunk_copy=chunk_copy)
    # Reproduce the failed second-stage materialization, including old cached
    # window metadata rather than requiring a rewrite of that window first.
    with Dataset(windows[0], "a") as ds:
        ds["gfs_forecast_lead_hours"].delncattr("units")
    result = tmp_path / "calendar.nc"
    create_daily_archive(windows, result, date(2003, 1, 1), expected_hours=2, chunk_copy=chunk_copy)
    with Dataset(result) as ds:
        assert ds["gfs_forecast_lead_hours"].units == "hours"
        assert ds["native_donor_distance_km"].units == "km"
        np.testing.assert_array_equal(ds["gfs_forecast_lead_hours"][:], [0, 1])
        for name in FIELDS:
            np.testing.assert_array_equal(ds[name][:, 0, 0], [0, 1])
    assert before == [p.read_bytes() for p in paths]
    if chunk_copy:
        assert json.loads(result.with_suffix(".nc.manifest.json").read_text())["archive_writer"] == "compressed_chunks"
    with Dataset(paths[1], "a") as ds:
        ds["gfs_forecast_lead_hours"].units = "seconds"
    with pytest.raises(ValueError, match="Conflicting diagnostic metadata"):
        create_daily_archive(paths, tmp_path / "reject.nc", date(2003, 1, 1),
                             expected_hours=2, chunk_copy=chunk_copy)
