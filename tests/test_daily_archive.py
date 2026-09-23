from datetime import date
from pathlib import Path

import numpy as np
import pytest
from netCDF4 import Dataset

from hydro_ops.forcing.daily_archive import (
    _digest,
    create_daily_archive,
    daily_archive_is_current,
    verified_daily_archive,
)


@pytest.mark.parametrize("chunk_copy", [False, True])
def test_cnrfc_policy_crosses_july_boundary_and_calendar_regrouping(tmp_path, chunk_copy):
    paths = []
    for hour in range(24):
        path = tmp_path / f"boundary-{hour}.nc"
        with Dataset(path, "w") as data:
            data.createDimension("time", 1)
            data.createDimension("y", 2)
            data.createDimension("x", 2)
            time = data.createVariable("time", "f8", ("time",))
            time.units = "hours since 2020-06-30 12:00:00"
            time[:] = hour
            field = data.createVariable("RAINRATE", "f4", ("time", "y", "x"),
                                        zlib=True, complevel=2, chunksizes=(1, 2, 2))
            field[:] = hour
            if hour >= 12:
                data.cnrfc_stage4_policy = "six-hour constraint; NLDAS-2 timing"
        paths.append(path)
    window = tmp_path / "window.nc"
    create_daily_archive(paths, window, date(2020, 7, 1), chunk_copy=chunk_copy)
    with Dataset(window) as data:
        assert data.cnrfc_stage4_policy == "six-hour constraint; NLDAS-2 timing"
        assert data.cnrfc_stage4_policy_effective_from == "2020-07-01T00:00:00Z"
        np.testing.assert_array_equal(data["RAINRATE"][:, 0, 0], np.arange(24))
    selected = tmp_path / "july.nc"
    create_daily_archive([window] * 12, selected, date(2020, 7, 1), expected_hours=12,
                         source_time_indices=list(range(12, 24)), chunk_copy=chunk_copy)
    with Dataset(selected) as data:
        assert data.cnrfc_stage4_policy == "six-hour constraint; NLDAS-2 timing"
        np.testing.assert_array_equal(data["RAINRATE"][:, 0, 0], np.arange(12, 24))
    with Dataset(paths[15], "a") as data:
        data.delncattr("cnrfc_stage4_policy")
    with pytest.raises(ValueError, match="Missing CNRFC policy"):
        create_daily_archive(paths, tmp_path / "invalid.nc", date(2020, 7, 1),
                             chunk_copy=chunk_copy)


@pytest.mark.parametrize("legacy_first", [True, False])
def test_optional_timing_schema_preserves_existing_and_marks_unknown(tmp_path, legacy_first):
    paths = [tmp_path / f"{h}.nc" for h in range(2)]
    for hour, path in enumerate(paths):
        with Dataset(path, "w") as data:
            data.createDimension("time", 1)
            data.createDimension("x", 2)
            time = data.createVariable("time", "f8", ("time",))
            time.units = "hours since 2003-01-01"
            time[:] = hour
            data.createVariable("precip_source_id", "u1", ("time", "x"))[:] = [[3, 7]]
            if (hour == 1) == legacy_first:
                data.createVariable("precip_timing_source_id", "u1", ("time", "x"))[:] = [[5, 6]]
    with pytest.raises(ValueError, match="schema differs"):
        create_daily_archive(paths, tmp_path / "reject.nc", date(2003, 1, 1), expected_hours=2)
    output = tmp_path / "normalized.nc"
    create_daily_archive(paths, output, date(2003, 1, 1), expected_hours=2,
                         normalize_precipitation_timing=True)
    with Dataset(output) as data:
        expected = [[0, 0], [5, 6]] if legacy_first else [[5, 6], [0, 0]]
        np.testing.assert_array_equal(data["precip_timing_source_id"][:], expected)
        np.testing.assert_array_equal(data["precip_source_id"][:], [[3, 7], [3, 7]])


def test_digest_ignores_storage_beneath_mask() -> None:
    first = np.ma.array([1.0, np.nan], mask=[False, True])
    second = np.ma.array([1.0, -9.99e8], mask=[False, True])
    assert _digest(first) == _digest(second)


def write_hour(path: Path, hour: int, *, static_offset: float = 0) -> None:
    with Dataset(path, "w", format="NETCDF3_CLASSIC") as dataset:
        dataset.createDimension("time", 1)
        dataset.createDimension("y", 2)
        dataset.createDimension("x", 3)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "hours since 2026-01-01 00:00:00"
        time[:] = hour
        latitude = dataset.createVariable("latitude", "f4", ("y",))
        latitude[:] = np.arange(2) + static_offset
        field = dataset.createVariable("field", "f4", ("time", "y", "x"))
        field.units = "K"
        field[:] = hour + np.arange(6).reshape(1, 2, 3)


def test_daily_archive_shares_coordinates_and_preserves_hours(tmp_path: Path) -> None:
    paths = []
    for hour in range(3):
        path = tmp_path / f"hour-{hour}.nc"
        write_hour(path, hour)
        paths.append(path)
    destination = tmp_path / "daily.nc"
    work = tmp_path / "scratch"
    create_daily_archive(
        paths,
        destination,
        date(2026, 1, 1),
        expected_hours=3,
        compression_level=4,
        work_directory=work,
    )
    with Dataset(destination) as dataset:
        assert dataset["field"].shape == (3, 2, 3)
        assert dataset["latitude"].shape == (2,)
        np.testing.assert_array_equal(dataset["field"][:, 0, 0], [0, 1, 2])
        assert dataset["field"].filters()["complevel"] == 4
    assert destination.with_suffix(".nc.manifest.json").is_file()
    manifest = destination.with_suffix(".nc.manifest.json").read_text()
    assert '"level": 4' in manifest
    assert daily_archive_is_current(paths, destination, date(2026, 1, 1))
    assert not list(work.iterdir())
    paths[0].touch()
    assert not daily_archive_is_current(paths, destination, date(2026, 1, 1))


def test_daily_archive_rejects_changed_static_grid(tmp_path: Path) -> None:
    paths = []
    for hour in range(2):
        path = tmp_path / f"hour-{hour}.nc"
        write_hour(path, hour, static_offset=hour)
        paths.append(path)
    try:
        create_daily_archive(paths, tmp_path / "daily.nc", date(2026, 1, 1), expected_hours=2)
    except ValueError as error:
        assert "Static variable latitude differs" in str(error)
    else:
        raise AssertionError("changed static grid was accepted")


def test_daily_archive_applies_time_variable_override_directly(tmp_path: Path) -> None:
    paths = []
    for hour in range(3):
        path = tmp_path / f"hour-{hour}.nc"
        write_hour(path, hour)
        paths.append(path)
    corrected = np.arange(18, dtype=np.float64).reshape(3, 2, 3) + 100.123456789
    destination = tmp_path / "daily.nc"
    create_daily_archive(
        paths,
        destination,
        date(2026, 1, 1),
        expected_hours=3,
        time_variable_overrides={"field": corrected},
        global_attributes={"constraint": "test"},
        verification="targeted",
    )
    with Dataset(destination) as dataset:
        np.testing.assert_array_equal(dataset["field"][:], corrected.astype(np.float32))
        assert dataset.getncattr("constraint") == "test"
    manifest = destination.with_suffix(".nc.manifest.json").read_text()
    assert '"overridden_time_variables": [' in manifest
    assert '"field"' in manifest
    assert '"verification": "targeted"' in manifest


def test_daily_archive_reads_selected_records_directly(tmp_path: Path) -> None:
    source = tmp_path / "source-daily.nc"
    with Dataset(source, "w") as dataset:
        dataset.createDimension("time", 4)
        dataset.createDimension("y", 2)
        dataset.createDimension("x", 3)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "hours since 2026-01-01 00:00:00"
        time[:] = np.arange(4)
        dataset.createVariable("latitude", "f4", ("y",))[:] = np.arange(2)
        field = dataset.createVariable("field", "f4", ("time", "y", "x"))
        field[:] = np.arange(4)[:, None, None] + np.arange(6).reshape(1, 2, 3)
    destination = tmp_path / "selected.nc"
    create_daily_archive(
        [source, source, source],
        destination,
        date(2026, 1, 1),
        expected_hours=3,
        source_time_indices=[1, 2, 3],
    )
    with Dataset(destination) as dataset:
        np.testing.assert_array_equal(dataset["time"][:], [1, 2, 3])
        np.testing.assert_array_equal(dataset["field"][:, 0, 0], [1, 2, 3])


def test_daily_archive_requires_complete_day(tmp_path: Path) -> None:
    try:
        create_daily_archive([], tmp_path / "daily.nc", date(2026, 1, 1))
    except ValueError as error:
        assert "Expected 24 hourly files" in str(error)
    else:
        raise AssertionError("incomplete day was accepted")


def test_verified_daily_archive_survives_hourly_source_removal(tmp_path: Path) -> None:
    paths = []
    for hour in range(24):
        path = tmp_path / f"hour-{hour}.nc"
        write_hour(path, hour)
        paths.append(path)
    destination = tmp_path / "daily.nc"
    day = date(2026, 1, 1)
    create_daily_archive(paths, destination, day)
    for path in paths:
        path.unlink()
    assert verified_daily_archive(destination, day)
