#!/usr/bin/env python3
"""Convert retained Stage-IV six-hour accumulations over an inclusive date range."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tarfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, num2date

from hydro_ops.config import load_settings
from hydro_ops.download.stage4_convert import CONUS_GRIB2, Stage4Converter
from hydro_ops.download.stage4_legacy import LEGACY_SIX_HOURLY, LegacyStage4Converter


def valid_daily(path: Path, day: date) -> bool:
    try:
        with Dataset(path) as dataset:
            time = dataset["time"]
            values = num2date(time[:], time.units, getattr(time, "calendar", "standard"))
            stamps = [(value.year, value.month, value.day, value.hour) for value in values]
            expected = [(day.year, day.month, day.day, hour) for hour in (0, 6, 12, 18)]
            return stamps == expected and np.asarray(dataset["APCP_surface"][:]).shape[0] == 4
    except (OSError, KeyError, ValueError):
        return False


def aggregate_day(settings, day: date) -> bool:
    directory = settings.stage4_data_dir / "netcdf/archive" / day.strftime("%Y/%m/%d")
    pieces = sorted(directory.glob(f"st4_conus.{day:%Y%m%d}??.06h.*.nc"))
    destination = (
        settings.stage4_data_dir / "netcdf/archive" / day.strftime("%Y/%m")
        / f"stage4_archive_06h.{day:%Y%m%d}.nc"
    )
    if valid_daily(destination, day):
        for piece in pieces:
            piece.unlink()
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
        return True
    by_hour: dict[int, Path] = {}
    for piece in pieces:
        match = re.search(r"\.\d{8}(\d{2})\.06h\.", piece.name)
        if match is None:
            continue
        hour = int(match.group(1))
        current = by_hour.get(hour)
        if current is None or ("grb2" in piece.name and "grb2" not in current.name):
            by_hour[hour] = piece
    if set(by_hour) != {0, 6, 12, 18}:
        return False
    selected_pieces = [by_hour[hour] for hour in (0, 6, 12, 18)]
    executable = shutil.which("ncrcat")
    if not executable:
        raise RuntimeError("Stage-IV six-hour aggregation requires ncrcat")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f"{destination.name}.part")
    partial.unlink(missing_ok=True)
    subprocess.run(
        [executable, "-O", "-4", "-L", "2", *map(str, selected_pieces), str(partial)],
        check=True,
    )
    partial.replace(destination)
    if not valid_daily(destination, day):
        raise RuntimeError(f"Invalid six-hour daily collection: {destination}")
    for piece in pieces:
        piece.unlink()
    if not any(directory.iterdir()):
        directory.rmdir()
    return True


def days(start: date, end: date):
    for offset in range((end - start).days + 1):
        yield start + timedelta(days=offset)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument(
        "--grid-template",
        type=Path,
        default=Path("forcing/static/noaa/stage4/stage4_cnrfcmask.nc"),
    )
    args = parser.parse_args()
    if args.end < args.start:
        parser.error("--end must not precede --start")
    settings = load_settings()
    modern = Stage4Converter(settings)
    legacy = LegacyStage4Converter(settings, args.grid_template)
    selected = converted = missing = incomplete = failed = 0
    for day in days(args.start, args.end):
        archive = (
            settings.stage4_data_dir / "archive" / day.strftime("%Y/%m")
            / f"ST4.{day:%Y%m%d}.tar"
        )
        if not archive.is_file():
            missing += 1
            continue
        with tarfile.open(archive) as bundle:
            flat_names = {
                Path(member.name).name
                for member in bundle
                if member.isfile() and member.name == Path(member.name).name
            }
        try:
            if any(
                name.endswith(".06h.grb2") and CONUS_GRIB2.fullmatch(name)
                for name in flat_names
            ):
                found, made = modern.convert_archive(archive, include_hourly=False)
            elif any(LEGACY_SIX_HOURLY.fullmatch(name) for name in flat_names):
                found, made = legacy.convert_daily_six_hour(archive)
            else:
                missing += 1
                continue
            selected += found
            converted += made
            if not aggregate_day(settings, day):
                incomplete += 1
        except (OSError, RuntimeError, subprocess.CalledProcessError, tarfile.TarError) as error:
            failed += 1
            print(f"failed_day={day.isoformat()} error={error}")
    print(
        f"selected={selected} converted={converted} missing_days={missing} "
        f"incomplete_days={incomplete} failed_days={failed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
