from pathlib import Path
from types import SimpleNamespace

from hydro_ops.download.stage4_convert import (
    CONUS_GRIB2,
    CONUS_HOURLY_GRIB2,
    Stage4Converter,
)


def test_conus_selection_excludes_other_domains_and_gifs():
    assert CONUS_GRIB2.fullmatch("st4_conus.2026081201.01h.grb2")
    assert CONUS_GRIB2.fullmatch("st4_conus.2026081212.24h.grb2")
    assert not CONUS_GRIB2.fullmatch("st4_ak.2026081200.06h.grb2")
    assert not CONUS_GRIB2.fullmatch("st4_conus.2026081201.01h.gif")
    assert CONUS_HOURLY_GRIB2.fullmatch("st4_conus.2026081201.01h.grb2")
    assert not CONUS_HOURLY_GRIB2.fullmatch("st4_conus.2026081212.24h.grb2")


def test_netcdf_destination_keeps_each_source_product_separate(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda value: f"/bin/{value}")
    settings = SimpleNamespace(stage4_wgrib2="wgrib2", stage4_data_dir=tmp_path)
    converter = Stage4Converter(settings)
    destination = converter.destination("st4_conus.2026081201.01h.grb2", "archive")
    assert destination == Path(
        tmp_path / "netcdf/archive/2026/08/12/st4_conus.2026081201.01h.grb2.nc"
    )
