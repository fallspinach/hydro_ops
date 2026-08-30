import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import xarray as xr

from hydro_ops.download.prism import (
    PrismDownloader,
    PrismRelease,
    normalize_monthly_netcdf,
)


def settings(tmp_path: Path):
    return SimpleNamespace(
        prism_base_url="https://prism.example/data/get",
        prism_data_dir=tmp_path / "data",
        prism_connect_timeout=3,
        prism_read_timeout=5,
        prism_retries=1,
        prism_request_delay=0,
        work_root=tmp_path / "work",
    )


def test_release_row():
    release = PrismRelease.from_row(
        ["2026-08-12", "2026-08-17", "ppt", "2", "https://example/grid"]
    )
    assert release.data_date == date(2026, 8, 12)
    assert release.release_date == date(2026, 8, 17)
    assert release.grid_count == 2
    assert release.element == "ppt"


def test_paths_and_matching_release_metadata(tmp_path):
    downloader = PrismDownloader(settings(tmp_path))
    release = PrismRelease(date(2026, 8, 12), date(2026, 8, 17), 2, "https://example/grid", "ppt")
    destination, metadata = downloader.paths(release.data_date)
    assert destination == tmp_path / "data/ppt/2026/08/prism_ppt_us_25m_20260812.nc"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        json.dumps({"release_date": "2026-08-17", "grid_count": 2, "normalization_version": 1})
    )
    assert downloader._metadata_matches(metadata, release)
    newer = PrismRelease(date(2026, 8, 12), date(2026, 9, 15), 3, "https://example/grid", "ppt")
    assert not downloader._metadata_matches(metadata, newer)


def test_normalize_monthly_prism_preserves_monthly_semantics(tmp_path):
    source = tmp_path / "raw.nc"
    xr.Dataset(
        {"Band1": (("lat", "lon"), np.ones((2, 3))), "crs": np.int32(0)}
    ).to_netcdf(source)
    destination = tmp_path / "monthly.nc"
    normalize_monthly_netcdf(source, destination, 1979, 2, "tmax")
    with xr.open_dataset(destination) as data:
        assert data.attrs["temporal_resolution"] == "monthly"
        assert data.attrs["time_coverage_end"] == "1979-03-01T00:00:00Z"
        assert data.tmax.attrs["cell_methods"] == (
            "time: maximum within days time: mean over days"
        )
        assert str(data.time.values[0]) == "1979-02-01T00:00:00.000000000"
