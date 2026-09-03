#!/usr/bin/env python3
"""Rechunk time-addressable daily forcing archives onto UTC calendar days."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from netCDF4 import Dataset, num2date

from hydro_ops.forcing.daily_archive import create_daily_archive


def candidate_paths(root: Path, day: date) -> list[Path]:
    paths: list[Path] = []
    for offset in (-1, 0, 1):
        label = day + timedelta(days=offset)
        stamp = label.strftime("%Y%m%d")
        for path in (
            root / label.strftime("%Y/%m") / f"{stamp}.LDASIN_DOMAIN1",
            root / label.strftime("%Y/%m") / f"{stamp}.LDASIN_DOMAIN1.nc",
        ):
            if path.is_file():
                paths.append(path)
    return paths


def locate_hours(root: Path, day: date) -> tuple[list[Path], list[int]]:
    wanted = {
        datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(hours=hour): hour
        for hour in range(24)
    }
    found: dict[int, tuple[Path, int]] = {}
    for path in candidate_paths(root, day):
        with Dataset(path) as data:
            time = data["time"]
            values = num2date(
                time[:],
                time.getncattr("units"),
                calendar=time.getncattr("calendar") if "calendar" in time.ncattrs() else "standard",
                only_use_cftime_datetimes=False,
                only_use_python_datetimes=True,
            )
        for index, value in enumerate(values):
            valid = value.replace(tzinfo=UTC)
            if valid in wanted:
                hour = wanted[valid]
                if hour in found:
                    raise ValueError(f"Duplicate forcing record for {valid.isoformat()}")
                found[hour] = (path, index)
    missing = sorted(set(range(24)).difference(found))
    if missing:
        raise FileNotFoundError(f"Missing UTC hours for {day}: {missing}")
    return [found[hour][0] for hour in range(24)], [found[hour][1] for hour in range(24)]


def prism_window_metadata(paths: list[Path], stream: str) -> dict[str, str]:
    """Validate and describe the two accepted PRISM windows forming a calendar day."""
    windows: list[dict[str, str]] = []
    for path in dict.fromkeys(paths):
        with Dataset(path) as data:
            accepted = str(getattr(data, "prism_reconciliation_accepted", "false")).lower()
            source_stream = str(getattr(data, "forcing_stream", ""))
            if accepted != "true":
                raise ValueError(f"PRISM window is not accepted: {path}")
            if source_stream and source_stream != stream:
                raise ValueError(f"PRISM window stream {source_stream!r} differs from {stream!r}")
            windows.append(
                {
                    "prism_day": str(data.getncattr("prism_day")),
                    "revision": str(data.getncattr("prism_precipitation_revision")),
                    "precipitation_source": str(data.getncattr("prism_precipitation_source")),
                    "source_window_file": str(path.resolve()),
                }
            )
    if len(windows) != 2:
        raise ValueError(f"Calendar publication requires two PRISM windows; found {len(windows)}")
    return {
        "forcing_stream": stream,
        "prism_constraint_frequency": "daily",
        "prism_reconciliation_accepted": "true",
        "prism_constraint_windows": json.dumps(windows, sort_keys=True),
        "prism_precipitation_revisions": json.dumps(
            {item["prism_day"]: item["revision"] for item in windows}, sort_keys=True
        ),
    }


def clean_window_attributes(path: Path, day: date) -> None:
    """Remove misleading single-window attributes after calendar-day publication."""
    misleading = (
        "prism_day",
        "forcing_window",
        "prism_precipitation_revision",
        "prism_precipitation_source",
        "prism_temperature_corrected_day",
        "valid_time",
    )
    with Dataset(path, "r+") as output:
        for name in misleading:
            if name in output.ncattrs():
                output.delncattr(name)
        output.setncattr("archive_period", day.isoformat())
        output.setncattr("archive_granularity", "utc_calendar_day")
        output.setncattr("calendar_day_start_utc", f"{day.isoformat()}T00:00:00Z")
        output.setncattr("calendar_day_end_utc", f"{day.isoformat()}T23:00:00Z")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--days", required=True, type=int)
    parser.add_argument("--work-directory", type=Path)
    parser.add_argument("--stream", choices=("nrt", "retro"))
    parser.add_argument("--require-accepted-prism-windows", action="store_true")
    parser.add_argument("--hierarchical", action="store_true")
    args = parser.parse_args()
    if args.days < 1:
        parser.error("--days must be positive")
    for offset in range(args.days):
        day = args.start + timedelta(days=offset)
        paths, indices = locate_hours(args.input_root, day)
        metadata = (
            prism_window_metadata(paths, args.stream)
            if args.require_accepted_prism_windows and args.stream
            else {}
        )
        if args.require_accepted_prism_windows and not args.stream:
            parser.error("--stream is required with --require-accepted-prism-windows")
        directory = args.output_root / day.strftime("%Y/%m") if args.hierarchical else args.output_root
        destination = directory / f"{day:%Y%m%d}.LDASIN_DOMAIN1"
        create_daily_archive(
            paths,
            destination,
            day,
            expected_hours=24,
            compression_level=2,
            work_directory=args.work_directory,
            source_time_indices=indices,
            verification="targeted",
            global_attributes={
                "calendar_day_materialization": "true",
                "calendar_day_source_root": str(args.input_root.resolve()),
                **metadata,
            },
        )
        clean_window_attributes(destination, day)
        print(destination, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
