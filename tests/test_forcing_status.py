from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from hydro_ops.forcing_status import forcing_coverage, format_coverage


def settings(tmp_path: Path):
    return SimpleNamespace(
        nldas_data_dir=tmp_path / "nldas",
        stage4_data_dir=tmp_path / "stage4",
        prism_data_dir=tmp_path / "prism",
        prism_variables=("ppt", "tmean"),
        hrrr_data_dir=tmp_path / "hrrr",
        mrms_data_dir=tmp_path / "mrms",
        mrms_products=("pass1", "pass2", "quality"),
    )


def touch(root: Path, relative: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def test_forcing_coverage_parses_latest_valid_times(tmp_path):
    config = settings(tmp_path)
    touch(config.nldas_data_dir, "2026/232/NLDAS_FORA0125_H.A20260820.2300.020.nc")
    touch(
        config.hrrr_data_dir,
        "2026/08/20/hrrr_forcing.2026082023.grib2.nc",
    )
    touch(
        config.mrms_data_dir,
        "netcdf/pass2/2026/08/20/"
        "MRMS_MultiSensor_QPE_01H_Pass2_00.00_20260820-220000.grib2.nc",
    )
    rows = {row.product: row for row in forcing_coverage(config)}
    assert rows["NLDAS-2"].latest == datetime(2026, 8, 20, 23, tzinfo=UTC)
    assert rows["HRRR forcing"].latest == datetime(2026, 8, 20, 23, tzinfo=UTC)
    assert rows["MRMS pass2"].latest == datetime(2026, 8, 20, 22, tzinfo=UTC)
    assert rows["MRMS pass1"].latest is None


def test_format_coverage_reports_age_and_missing():
    from hydro_ops.forcing_status import Coverage

    rows = [
        Coverage("recent", datetime(2026, 8, 20, 20, tzinfo=UTC), 24),
        Coverage("absent", None, 0),
    ]
    report = format_coverage(rows, now=datetime(2026, 8, 20, 22, tzinfo=UTC))
    assert "2.0 h" in report
    assert "missing" in report
    assert "24" in report
