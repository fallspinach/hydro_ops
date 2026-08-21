from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from hydro_ops.download.mrms import MrmsDownloader, is_gzip


def settings(tmp_path: Path):
    return SimpleNamespace(
        mrms_base_url="https://archive.example",
        mrms_data_dir=tmp_path,
        mrms_products=("pass1", "pass2", "quality"),
        mrms_download_jobs=2,
        mrms_retries=1,
        mrms_connect_timeout=3,
        mrms_read_timeout=5,
        mrms_wgrib2="wgrib2",
    )


def test_pass1_url_and_layout(tmp_path):
    valid = datetime(2026, 8, 20, 4, tzinfo=UTC)
    item = MrmsDownloader(settings(tmp_path)).file("pass1", valid)
    assert item.url == (
        "https://archive.example/CONUS/MultiSensor_QPE_01H_Pass1_00.00/20260820/"
        "MRMS_MultiSensor_QPE_01H_Pass1_00.00_20260820-040000.grib2.gz"
    )
    assert item.compressed == (
        tmp_path
        / "raw/pass1/2026/08/20/"
        "MRMS_MultiSensor_QPE_01H_Pass1_00.00_20260820-040000.grib2.gz"
    )
    assert item.netcdf.name.endswith(".grib2.nc")


def test_pass2_and_quality_have_separate_paths(tmp_path):
    downloader = MrmsDownloader(settings(tmp_path))
    valid = datetime(2026, 8, 20, 4, tzinfo=UTC)
    pass2 = downloader.file("pass2", valid)
    quality = downloader.file("quality", valid)
    assert "MultiSensor_QPE_01H_Pass2_00.00" in pass2.url
    assert "RadarAccumulationQualityIndex_01H_00.00" in quality.url
    assert pass2.netcdf.parent != quality.netcdf.parent


def test_best_available_prefers_pass2(tmp_path, monkeypatch):
    downloader = MrmsDownloader(settings(tmp_path))
    valid = datetime(2026, 8, 20, 4, tzinfo=UTC)
    pass1 = downloader.file("pass1", valid).netcdf
    pass2 = downloader.file("pass2", valid).netcdf
    available = {pass1}
    monkeypatch.setattr("hydro_ops.download.mrms.is_netcdf", lambda path: path in available)
    assert downloader.best_available(valid) == pass1
    available.add(pass2)
    assert downloader.best_available(valid) == pass2


def test_gzip_magic(tmp_path):
    path = tmp_path / "sample.gz"
    path.write_bytes(b"\x1f\x8bcontent")
    assert is_gzip(path)
    path.write_text("error page")
    assert not is_gzip(path)
