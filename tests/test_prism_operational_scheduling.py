from __future__ import annotations

import importlib.util
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, date2num


def _load_script(name: str):
    path = Path(__file__).parents[1] / "bin" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_for_day_tracks_prism_lifecycle() -> None:
    scheduler = _load_script("submit_prism_forcing_updates")
    today = date(2026, 8, 25)

    assert scheduler.revision_for_day(date(2026, 8, 1), today) == "early"
    assert scheduler.revision_for_day(date(2026, 7, 31), today) == "provisional"
    assert scheduler.revision_for_day(date(2026, 2, 23), today) == "stable"


def test_streams_retain_mutable_and_stable_revisions_separately() -> None:
    scheduler = _load_script("submit_prism_forcing_updates")
    today = date(2026, 8, 25)

    assert scheduler.revision_for_stream(date(2026, 8, 1), today, "nrt") == "early"
    assert scheduler.revision_for_stream(date(2026, 7, 31), today, "nrt") == "provisional"
    assert scheduler.revision_for_stream(date(2026, 2, 23), today, "nrt") is None
    assert scheduler.revision_for_stream(date(2026, 7, 31), today, "retro") is None
    assert scheduler.revision_for_stream(date(2026, 2, 23), today, "retro") == "stable"


def test_scan_windows_target_recent_and_newly_stable_days() -> None:
    scheduler = _load_script("submit_prism_forcing_updates")
    today = date(2026, 8, 25)

    assert scheduler.scan_window(today, "nrt", 10, 1) == (
        date(2026, 8, 15),
        date(2026, 8, 24),
    )
    assert scheduler.scan_window(today, "retro", 45, 1) == (
        date(2026, 1, 10),
        date(2026, 2, 23),
    )


def test_daily_record_lookup_uses_actual_time_coordinate(tmp_path: Path) -> None:
    driver = _load_script("produce_prism_constrained_daily")
    archive = tmp_path / "2026/07/20260715.LDASIN_DOMAIN1.nc"
    archive.parent.mkdir(parents=True)
    units = "hours since 1970-01-01 00:00:00"
    times = [datetime(2026, 7, 14, 12, tzinfo=UTC), datetime(2026, 7, 15, 0, tzinfo=UTC)]
    with Dataset(archive, "w") as data:
        data.createDimension("time", 2)
        variable = data.createVariable("time", "f8", ("time",))
        variable.units = units
        variable[:] = np.asarray(date2num(times, units))

    assert driver.find_daily_record(
        tmp_path, datetime(2026, 7, 15, 0, tzinfo=UTC)
    ) == (archive, 1)
    assert driver.find_daily_record(
        tmp_path, datetime(2026, 7, 15, 1, tzinfo=UTC)
    ) is None
