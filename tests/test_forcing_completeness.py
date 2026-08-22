from __future__ import annotations

from datetime import date
from pathlib import Path

from hydro_ops.forcing.completeness import hrrr_day, nldas2_day, prism_day, report_range


def touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")


def test_nldas_and_hrrr_hourly_completeness(tmp_path: Path) -> None:
    day = date(2022, 10, 1)
    nldas_root = tmp_path / "nldas"
    hrrr_root = tmp_path / "hrrr"
    for hour in range(23):
        touch(
            nldas_root
            / "2022/274"
            / f"NLDAS_FORA0125_H.A20221001.{hour:02d}00.020.nc"
        )
        touch(
            hrrr_root
            / "2022/10/01"
            / f"hrrr_forcing.20221001{hour:02d}.grib2.nc"
        )
    nldas = nldas2_day(nldas_root, day)
    hrrr = hrrr_day(hrrr_root, day)
    assert nldas.present == hrrr.present == 23
    assert nldas.missing == hrrr.missing == ("2022-10-01T23:00Z",)


def test_prism_and_range_report(tmp_path: Path) -> None:
    root = tmp_path / "prism"
    touch(root / "tmin/2022/10/prism_tmin_us_25m_20221001.nc")
    assert prism_day(root, date(2022, 10, 1), "tmin").complete
    assert not prism_day(root, date(2022, 10, 2), "tmin").complete
    reports = report_range("prism_tmin", root, date(2022, 10, 1), date(2022, 10, 2))
    assert [report.complete for report in reports] == [True, False]
