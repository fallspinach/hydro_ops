#!/usr/bin/env python3
"""Create verified daily collections from hourly forcing-conversion files."""

from __future__ import annotations

import argparse
import concurrent.futures
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from hydro_ops.config import load_settings
from hydro_ops.forcing.daily_archive import (
    create_daily_archive,
    daily_archive_is_current,
    verified_daily_archive,
)
from hydro_ops.work import temporary_work_root


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def days(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def layout(product: str, stream: str | None, day: date) -> tuple[list[Path], Path]:
    settings = load_settings()
    stamp = day.strftime("%Y/%m/%d")
    month = day.strftime("%Y/%m")
    compact = day.strftime("%Y%m%d")
    if product == "nldas2":
        hourly = settings.nldas_data_dir / day.strftime("%Y/%j")
        paths = sorted(hourly.glob(f"NLDAS_FORA0125_H.A{compact}.????.*.nc*"))
        destination = (
            settings.nldas_data_dir / day.strftime("%Y") /
            f"NLDAS_FORA0125_H.A{compact}.020.nc"
        )
    elif product == "hrrr":
        hourly = settings.hrrr_data_dir / stamp
        paths = sorted(hourly.glob(f"hrrr_forcing.{compact}??.grib2.nc"))
        destination = settings.hrrr_data_dir / month / f"hrrr_forcing.{compact}.nc"
    elif product.startswith("mrms_"):
        mrms_product = product.removeprefix("mrms_")
        hourly = settings.mrms_data_dir / "netcdf" / mrms_product / stamp
        paths = sorted(hourly.glob(f"*_{compact}-??????.grib2.nc"))
        destination = (
            settings.mrms_data_dir
            / "netcdf"
            / mrms_product
            / month
            / f"mrms_{mrms_product}.{compact}.nc"
        )
    else:
        assert product == "stage4" and stream
        hourly = settings.stage4_data_dir / "netcdf" / stream / stamp
        paths = sorted(hourly.glob(f"st4_conus.{compact}??.01h.grb2.nc"))
        destination = (
            settings.stage4_data_dir
            / "netcdf"
            / stream
            / month
            / f"stage4_{stream}_01h.{compact}.nc"
        )
    return paths, destination


def archive_one(arguments: tuple[str, str | None, date, bool, int, date]) -> tuple[str, bool]:
    """Archive one day in an isolated worker process."""
    product, stream, day, delete_hourly, minimum_age_days, today = arguments
    paths, destination = layout(product, stream, day)
    if not paths and verified_daily_archive(destination, day):
        return f"SKIP   {day}: verified daily archive present; hourly staging removed", True
    if daily_archive_is_current(paths, destination, day):
        return f"SKIP   {day}: verified daily archive is current", True
    compression_level = 4 if product.startswith("mrms_") else 2
    work_root = temporary_work_root(load_settings(), f"daily-{product}")
    try:
        create_daily_archive(
            paths,
            destination,
            day,
            compression_level=compression_level,
            work_directory=work_root,
        )
    except (OSError, RuntimeError, ValueError) as error:
        return f"FAILED {day}: {error}", False
    messages = [f"READY  {day}: {destination}"]
    if delete_hourly:
        if (today - day).days < minimum_age_days:
            messages.append(f"KEEP   {day}: inside {minimum_age_days}-day retention window")
        else:
            for path in paths:
                path.unlink()
            messages.append(f"DELETE {day}: removed {len(paths)} verified hourly NetCDF files")
            hourly_directory = paths[0].parent if paths else None
            if hourly_directory and hourly_directory.is_dir():
                try:
                    hourly_directory.rmdir()
                    messages.append(f"RMDIR  {day}: removed empty hourly staging directory")
                except OSError:
                    pass
    return "\n".join(messages), True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "product",
        choices=("nldas2", "hrrr", "mrms_pass1", "mrms_pass2", "mrms_quality", "stage4"),
    )
    parser.add_argument("--stream", choices=("archive", "realtime"))
    parser.add_argument("--start", required=True, type=parse_date)
    parser.add_argument("--end", required=True, type=parse_date)
    parser.add_argument("--delete-hourly", action="store_true")
    parser.add_argument("--minimum-age-days", type=int, default=31)
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--jobs", type=int, default=1)
    args = parser.parse_args()
    if args.product == "stage4" and not args.stream:
        parser.error("--stream is required for Stage IV")
    if args.delete_hourly and args.minimum_age_days < 0:
        parser.error("--minimum-age-days cannot be negative")
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    failures = 0
    today = datetime.now(UTC).date()
    work = [
        (args.product, args.stream, day, args.delete_hourly, args.minimum_age_days, today)
        for day in days(args.start, args.end)
    ]
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.jobs) as executor:
        for message, succeeded in executor.map(archive_one, work):
            print(message, file=sys.stdout if succeeded else sys.stderr, flush=True)
            failures += not succeeded
    return int(bool(failures) and not args.allow_incomplete)


if __name__ == "__main__":
    sys.exit(main())
