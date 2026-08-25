from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

from netCDF4 import Dataset


def _module():
    path = Path(__file__).parents[1] / "bin/cleanup_archived_forcing.py"
    spec = importlib.util.spec_from_file_location("cleanup_archived_forcing", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_inspect_allows_removed_hourly_sources_with_complete_daily_archive(
    tmp_path: Path,
) -> None:
    module = _module()
    day = date(2020, 12, 3)
    month = tmp_path / "2020/12"
    month.mkdir(parents=True)
    daily = month / "hrrr_forcing.20201203.nc"
    with Dataset(daily, "w") as data:
        data.createDimension("time", 24)
        data.createVariable("time", "i4", ("time",))[:] = range(24)
    hourly = [
        month / "03" / f"hrrr_forcing.20201203{hour:02d}.grib2.nc"
        for hour in range(24)
    ]
    raw = hourly[0].with_suffix("")
    raw.parent.mkdir()
    raw.write_bytes(b"GRIB")
    manifest = {
        "verified": True,
        "day": day.isoformat(),
        "source_files": [
            {"path": str(path), "bytes": 1, "mtime": 1.0} for path in hourly
        ],
    }
    daily.with_suffix(".nc.manifest.json").write_text(json.dumps(manifest))
    result = module.inspect(daily, cutoff=day)
    assert result["eligible"] is True
    assert result["hourly"] == []
    assert result["raw"] == [raw]


def test_inspect_rejects_partially_removed_hourly_sources(tmp_path: Path) -> None:
    module = _module()
    day = date(2020, 12, 3)
    daily = tmp_path / "hrrr_forcing.20201203.nc"
    with Dataset(daily, "w") as data:
        data.createDimension("time", 24)
    hourly = [tmp_path / f"hrrr_forcing.20201203{hour:02d}.grib2.nc" for hour in range(24)]
    hourly[0].write_bytes(b"x")
    daily.with_suffix(".nc.manifest.json").write_text(
        json.dumps(
            {
                "verified": True,
                "day": day.isoformat(),
                "source_files": [
                    {"path": str(path), "bytes": 1, "mtime": path.stat().st_mtime if path.exists() else 1.0}
                    for path in hourly
                ],
            }
        )
    )
    try:
        module.inspect(daily, cutoff=day)
    except ValueError as error:
        assert "Only some manifest sources remain" in str(error)
    else:
        raise AssertionError("partial source deletion was not rejected")
