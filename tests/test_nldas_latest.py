from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
import requests
from netCDF4 import Dataset

from hydro_ops import cli
from hydro_ops.download.nldas2 import Granule, Nldas2Downloader


def settings(tmp_path):
    return SimpleNamespace(nldas_data_dir=tmp_path, nldas_base_url="https://example.test",
                           nldas_connect_timeout=3, nldas_read_timeout=5,
                           nldas_download_jobs=2, work_root=tmp_path / "work")


@pytest.mark.parametrize("code,allowed,skip", [(404, True, True), (404, False, False),
                                               (401, True, False), (500, True, False)])
def test_only_probe_404_is_nonfatal(tmp_path, monkeypatch, code, allowed, skip):
    response = requests.Response()
    response.status_code = code
    class Session:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, *args, **kwargs):
            return response
    downloader = Nldas2Downloader(settings(tmp_path), check_credentials=False)
    monkeypatch.setattr(downloader, "_session", Session)
    if skip:
        assert downloader.discover(date(2026, 9, 20), allow_unpublished=allowed) == []
    else:
        with pytest.raises(requests.HTTPError):
            downloader.discover(date(2026, 9, 20), allow_unpublished=allowed)


def test_partial_then_complete_day(tmp_path, monkeypatch):
    downloader = Nldas2Downloader(settings(tmp_path), check_credentials=False)
    day = date(2026, 9, 20)
    granules = []
    for hour in range(24):
        path = tmp_path / f"hour-{hour:02}.nc"
        with Dataset(path, "w") as ds:
            ds.createDimension("time", 1)
            t = ds.createVariable("time", "f8", ("time",))
            t.units = "hours since 2026-09-20 00:00:00"
            t[:] = hour
            ds.createVariable("Tair", "f4", ("time",))[:] = 280 + hour
        granules.append(Granule("https://example.test/hour", path))
    available = granules[:13]
    monkeypatch.setattr(downloader, "discover", lambda *a, **kw: available)
    monkeypatch.setattr(downloader, "download_one", lambda g: "skipped")
    daily = tmp_path / "2026/NLDAS_FORA0125_H.A20260920.020.nc"
    assert downloader.download_day(day, aggregate_complete=True) == (13, 0)
    assert not daily.exists()
    available = granules
    assert downloader.download_day(day, aggregate_complete=True) == (24, 0)
    with Dataset(daily) as ds:
        assert len(ds.dimensions["time"]) == 24
        assert ds["Tair"][12] == 292
    assert all(g.destination.exists() for g in granules)
    monkeypatch.setattr(downloader, "discover", lambda *a, **kw: pytest.fail("verified archive rediscovered"))
    assert downloader.download_day(day, aggregate_complete=True) == (24, 0)


@pytest.mark.parametrize("latest", [False, True])
def test_cli_probe_range_and_historical_strictness(monkeypatch, latest):
    today = datetime.now(UTC).date()
    cutoff = today - timedelta(days=5)
    calls = []
    class Downloader:
        def __init__(self, *args, **kwargs):
            pass
        def download_day(self, day, **kwargs):
            calls.append((day, kwargs))
    monkeypatch.setattr(cli, "load_settings", lambda: SimpleNamespace(nldas_lag_days=5))
    monkeypatch.setattr(cli, "Nldas2Downloader", Downloader)
    args = cli.build_parser().parse_args(["download", "nldas2", "--start",
        str(cutoff - timedelta(days=1)), "--end", str(cutoff)] +
        (["--discover-latest"] if latest else []))
    assert cli.download_nldas2(args) == 0
    assert calls[-1][0] == (today if latest else cutoff)
    assert all(kw["allow_unpublished"] == (latest and day > cutoff) for day, kw in calls)
    assert all(kw["aggregate_complete"] == latest for _, kw in calls)
