# NetCDF and model dates here are timezone-naive UTC.
# ruff: noqa: DTZ001
from datetime import datetime

import netCDF4 as nc
import numpy as np
import pytest

from hydro_ops.wrf_hydro.production import months, namelist, publish_hourly


def test_month_segments():
    segments = list(months(datetime(1979, 1, 2), datetime(1980, 1, 1)))
    assert len(segments) == 12
    assert segments[0] == (datetime(1979, 1, 2), datetime(1979, 2, 1))
    assert next(months(datetime(1980, 2, 1), datetime(1980, 3, 1)))[1].day == 1


def test_namelist(tmp_path):
    path = tmp_path / "hydro.namelist"
    path.write_text("&HYDRO_nlist\n t0OutputFlag = 1 ! old\n/\n")
    namelist(path, {"t0OutputFlag": 0, "LDASOUT_DAILY": 1})
    assert "t0OutputFlag = 0" in path.read_text()
    assert "LDASOUT_DAILY = 1" in path.read_text()


def make_hour(path, hour):
    with nc.Dataset(path, "w") as ds:
        ds.createDimension("time", None)
        var = ds.createVariable("time", "f8", ("time",))
        var.units = "hours since 1979-02-01 00:00:00"
        var[:] = [hour]
        var.valid_min = float(hour)
        var.valid_max = float(hour)
        ds.createVariable("streamflow", "f4", ("time",))[:] = [hour + 1]


def test_calendar_boundary_merge(tmp_path):
    import sys
    from pathlib import Path

    ncrcat = Path(sys.executable).parent / "ncrcat"
    if not ncrcat.exists():
        pytest.skip("NCO not installed")
    inputs = []
    for hour in range(24):
        path = tmp_path / f"19790201{hour:02d}00.CHRTOUT_DOMAIN1"
        make_hour(path, hour)
        inputs.append(path)
    output = tmp_path / "hourly"
    publish_hourly(inputs[:1], output, ncrcat)
    publish_hourly(inputs[1:], output, ncrcat)
    archive = output / "1979/02/19790201.CHRTOUT_DOMAIN1"
    with nc.Dataset(archive) as ds:
        np.testing.assert_array_equal(ds["time"][:], np.arange(24))
        assert ds.calendar_day_complete == 1
        assert not np.ma.getmaskarray(ds["time"][:]).any()
        assert ds["time"].valid_min == 0
        assert ds["time"].valid_max == 23
        assert ds.model_total_valid_times == 24
        np.testing.assert_array_equal(ds["streamflow"][:], np.arange(24) + 1)
    with pytest.raises(ValueError, match="Refusing overlap"):
        publish_hourly(inputs, output, ncrcat)


def test_time_metadata_fix_rejects_incorrect_coordinates(tmp_path):
    from hydro_ops.wrf_hydro.production import normalize_archive_time

    path = tmp_path / "bad.nc"
    make_hour(path, 2)
    with nc.Dataset(path, "r+") as ds:
        with pytest.raises(ValueError, match="raw timestamp"):
            normalize_archive_time(ds, [datetime(1979, 2, 1, 1)])
        assert ds["time"].valid_max == 2


def test_initialization_preserves_counter_and_physics(tmp_path):
    from hydro_ops.wrf_hydro.production import ACCUMULATIONS, initialization, restart_names

    donor = tmp_path / "nwm/restarts/conus/retro/1986/01"
    donor.mkdir(parents=True)
    land, hydro = [donor / n for n in restart_names(datetime(1986, 1, 1))]
    with nc.Dataset(land, "w") as ds:
        ds.createDimension("Time", 1)
        ds.createDimension("DateStrLen", 19)
        ds.createVariable("Times", "S1", ("Time", "DateStrLen"))[:] = np.asarray(
            list("1986-01-01_00:00:00"), dtype="S1"
        )[None, :]
        for name in {*ACCUMULATIONS, "SMC", "SH2O", "SOIL_T", "SNEQV"}:
            ds.createVariable(name, "f4", ("Time",))[:] = 3
    with nc.Dataset(hydro, "w") as ds:
        ds.createDimension("links", 2)
        ds.Restart_Time = "1986-01-01_00:00:00"
        ds.Since_Date = "1981-01-01_00:00:00"
        ds.his_out_counts = 43820
        for name in ("hlink", "qlink1", "qlink2", "z_gwsubbas"):
            ds.createVariable(name, "f4", ("links",))[:] = [1, 2]
    targets = initialization(tmp_path, tmp_path / "init")
    with nc.Dataset(targets[1]) as ds:
        assert ds.his_out_counts == 43820
        assert ds.Restart_Time == "1979-01-02_00:00:00"
        np.testing.assert_array_equal(ds["hlink"][:], [1, 2])
    with nc.Dataset(targets[0]) as ds:
        assert ds["ACCPRCP"][0] == 0
        assert ds["SMC"][0] == 3
    with nc.Dataset(hydro) as ds:
        assert ds.Restart_Time == "1986-01-01_00:00:00"
    with nc.Dataset(land) as ds:
        assert ds["ACCPRCP"][0] == 3
    assert initialization(tmp_path, tmp_path / "init") == targets
    with nc.Dataset(targets[1], "r+") as ds:
        ds.his_out_counts = 0
    with pytest.raises(ValueError, match="Cached initialization"):
        initialization(tmp_path, tmp_path / "init")


def test_acceptance_requires_continuation(tmp_path, monkeypatch):
    import json
    import sys

    from hydro_ops.wrf_hydro import production

    monkeypatch.setenv("SLURM_JOB_USER", "test")
    monkeypatch.setenv("SLURM_JOB_ID", "1")
    monkeypatch.setenv("SLURM_NTASKS", "120")
    monkeypatch.setattr(
        sys, "argv", ["runner", "--project", str(tmp_path), "--campaign", "test", "--test"]
    )
    donor = [tmp_path / "land", tmp_path / "hydro"]
    monkeypatch.setattr(production, "initialization", lambda *a: donor)
    calls = []

    def segment(*args):
        calls.append((args[5], args[6], args[7]))
        return [tmp_path / f"land{len(calls)}", tmp_path / f"hydro{len(calls)}"]

    monkeypatch.setattr(production, "run_segment", segment)
    production.main()
    assert len(calls) == 2
    assert calls[0] == (donor, datetime(1979, 1, 2), datetime(1979, 1, 4))
    assert calls[1][0][0].name == "land1"
    assert calls[1][1:] == (datetime(1979, 1, 4), datetime(1979, 1, 5))
    gate = tmp_path / "nwm/runs/conus/retro/test/test-passed.json"
    assert json.loads(gate.read_text())["continuation_passed"]
    gate.unlink()

    def fail_continuation(*args):
        if args[6] == datetime(1979, 1, 4):
            raise RuntimeError("continuation failed")
        return donor

    monkeypatch.setattr(production, "run_segment", fail_continuation)
    with pytest.raises(RuntimeError, match="continuation failed"):
        production.main()
    assert not gate.exists()
