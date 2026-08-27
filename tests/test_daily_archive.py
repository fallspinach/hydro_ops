from datetime import date
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.forcing.daily_archive import (
    _digest,
    create_daily_archive,
    daily_archive_is_current,
    verified_daily_archive,
)


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
