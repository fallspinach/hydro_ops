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
