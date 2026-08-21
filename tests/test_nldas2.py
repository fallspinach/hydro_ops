from datetime import date

import pytest

from hydro_ops.download.nldas2 import is_netcdf, iter_dates


def test_iter_dates_is_inclusive():
    assert list(iter_dates(date(2024, 2, 28), date(2024, 3, 1))) == [
        date(2024, 2, 28),
        date(2024, 2, 29),
        date(2024, 3, 1),
    ]


def test_iter_dates_rejects_reverse_range():
    with pytest.raises(ValueError, match="after"):
        list(iter_dates(date(2024, 3, 1), date(2024, 2, 29)))


def test_netcdf_magic(tmp_path):
    path = tmp_path / "sample.nc"
    path.write_bytes(b"\x89HDF\r\n\x1a\ncontent")
    assert is_netcdf(path)
    path.write_text("login page")
    assert not is_netcdf(path)
