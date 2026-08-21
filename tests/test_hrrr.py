from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from hydro_ops.download.hrrr import (
    PRECIPITATION_RECORD,
    HrrrDownloader,
    parse_index,
    select_record,
)

INDEX = """1:0:d=2026081500:TMP:2 m above ground:anl:
2:100:d=2026081500:TMP:surface:anl:
3:180:d=2026081500:APCP:surface:0-1 hour acc fcst:
4:260:d=2026081500:LAND:surface:anl:
"""


def settings(tmp_path: Path):
    return SimpleNamespace(
        hrrr_base_url="https://archive.example",
        hrrr_data_dir=tmp_path,
        hrrr_download_jobs=2,
        hrrr_retries=1,
        hrrr_connect_timeout=3,
        hrrr_read_timeout=5,
        hrrr_wgrib2="wgrib2",
    )


def test_parse_index_builds_inclusive_byte_ranges():
    records = parse_index(INDEX)
    assert records[0].offset == 0
    assert records[0].end == 99
    assert records[2].end == 259


def test_select_record_matches_variable_level_and_timing():
    record = select_record(parse_index(INDEX), ("TMP", "2 m above ground", "anl"))
    assert record.number == 1
    assert select_record(parse_index(INDEX), PRECIPITATION_RECORD).number == 3


def test_select_record_rejects_missing_or_ambiguous_record():
    with pytest.raises(RuntimeError, match="found 0"):
        select_record(parse_index(INDEX), ("VGRD", "10 m above ground", "anl"))
    duplicate = INDEX + "5:300:d=2026081500:TMP:2 m above ground:anl:\n"
    with pytest.raises(RuntimeError, match="found 2"):
        select_record(parse_index(duplicate), ("TMP", "2 m above ground", "anl"))


def test_urls_and_destination_layout(tmp_path):
    downloader = HrrrDownloader(settings(tmp_path))
    valid = datetime(2026, 8, 15, 0, tzinfo=UTC)
    assert downloader.source_url(valid, 0) == (
        "https://archive.example/hrrr.20260815/conus/hrrr.t00z.wrfsfcf00.grib2"
    )
    assert downloader.source_url(valid, 1).endswith("hrrr.t00z.wrfsfcf01.grib2")
    assert downloader.destination(valid, "") == (
        tmp_path / "2026/08/15/hrrr_forcing.2026081500.grib2"
    )
    assert downloader.destination(valid, ".nc").name == "hrrr_forcing.2026081500.grib2.nc"
