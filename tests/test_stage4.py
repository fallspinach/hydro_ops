from datetime import date
from pathlib import Path
from types import SimpleNamespace

from hydro_ops.download.stage4 import Stage4Downloader, is_grib2, is_tar


def settings(tmp_path: Path):
    return SimpleNamespace(
        stage4_realtime_base_url="https://realtime.example",
        stage4_archive_base_url="https://archive.example",
        stage4_data_dir=tmp_path,
        stage4_download_jobs=2,
        stage4_retries=1,
        stage4_connect_timeout=3,
        stage4_read_timeout=5,
        stage4_wgrib2="wgrib2",
        work_root=tmp_path / "work",
    )


def test_archive_layout(tmp_path):
    item = Stage4Downloader(settings(tmp_path)).archive_file(date(2026, 8, 12))
    assert item.url == "https://archive.example/202608/ST4.20260812.tar"
    assert item.destination == tmp_path / "archive/2026/08/ST4.20260812.tar"


def test_archive_discovery_accepts_legacy_and_modern_names(tmp_path, monkeypatch):
    class Response:
        text = (
            '<a href="ST4.20210101">old</a>'
            '<a href="ST4.20210102.tar">new</a>'
            '<a href="ST2.20210101">stage2</a>'
        )

        def raise_for_status(self):
            pass

    class Session:
        calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, *args, **kwargs):
            self.calls += 1
            return Response()

    session = Session()
    downloader = Stage4Downloader(settings(tmp_path))
    monkeypatch.setattr(downloader, "_session", lambda: session)
    legacy = downloader.discover_archive(date(2021, 1, 1))
    modern = downloader.discover_archive(date(2021, 1, 2))
    assert legacy.url.endswith("/202101/ST4.20210101")
    assert legacy.destination.name == "ST4.20210101.tar"
    assert modern.url.endswith("/202101/ST4.20210102.tar")
    assert session.calls == 1


def test_archive_discovery_reports_missing_day(tmp_path, monkeypatch):
    class Response:
        text = '<a href="ST4.20210101">old</a>'

        def raise_for_status(self):
            pass

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, *args, **kwargs):
            return Response()

    downloader = Stage4Downloader(settings(tmp_path))
    monkeypatch.setattr(downloader, "_session", Session)
    import pytest

    with pytest.raises(FileNotFoundError, match="2021-01-02"):
        downloader.discover_archive(date(2021, 1, 2))


def test_grib2_magic(tmp_path):
    path = tmp_path / "sample.grb2"
    path.write_bytes(b"GRIB" + b"content-data")
    assert is_grib2(path)
    path.write_text("error page")
    assert not is_grib2(path)


def test_tar_validation(tmp_path):
    path = tmp_path / "sample.tar"
    import tarfile

    with tarfile.open(path, "w"):
        pass
    assert is_tar(path)
    path.write_text("error page")
    assert not is_tar(path)
