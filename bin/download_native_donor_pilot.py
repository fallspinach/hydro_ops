"""Acquire exact pilot inputs without changing operational source archives."""

from dataclasses import replace
from datetime import date
from pathlib import Path

from hydro_ops.config import load_settings
from hydro_ops.download.nldas2 import Nldas2Downloader

root = Path(__file__).resolve().parents[1]
pilot = root / "forcing/work/native-donor-pilot-20260825"
settings = replace(load_settings(), nldas_data_dir=pilot / "inputs/nldas2",
                   nldas_cookies=pilot / "earthdata-cookies.txt", nldas_download_jobs=4)
downloader = Nldas2Downloader(settings)
for day in (date(2026, 8, 23), date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 26), date(2026, 8, 27)):
    print(day, downloader.download_day(day), flush=True)
