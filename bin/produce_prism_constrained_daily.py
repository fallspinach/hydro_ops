#!/usr/bin/env python3
"""Publish one fully PRISM-constrained 12Z-to-12Z daily NWM forcing file."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, num2date

from hydro_ops.config import load_settings
from hydro_ops.forcing.operations import OperationalLayout
from hydro_ops.forcing.prism_temperature import (
    apply_daily_temperature_constraint,
    create_prism_temperature_constraints,
)
from hydro_ops.work import temporary_work_root


def prism_window(day: date) -> list[datetime]:
    start = datetime.combine(day - timedelta(days=1), time(12), tzinfo=UTC)
    return [start + timedelta(hours=index) for index in range(24)]


def forcing_path(root: Path, valid_time: datetime) -> Path:
    flat = root / valid_time.strftime("%Y%m%d%H.LDASIN_DOMAIN1")
    if flat.is_file():
        return flat
    return root / valid_time.strftime("%Y/%m/%d/%Y%m%d%H.LDASIN_DOMAIN1")


def daily_candidates(root: Path, valid_time: datetime) -> list[Path]:
    candidates: set[Path] = set()
    for offset in (-1, 0, 1):
        label = (valid_time + timedelta(days=offset)).date()
        stamp = label.strftime("%Y%m%d")
        candidates.update(
            {
                root / label.strftime("%Y/%m") / f"{stamp}.LDASIN_DOMAIN1.nc",
                root / label.strftime("%Y") / f"{stamp}.LDASIN_DOMAIN1.nc",
                root / label.strftime("%Y/%m/%d") / f"{stamp}.LDASIN_DOMAIN1.nc",
            }
        )
    return sorted(path for path in candidates if path.is_file())


def find_daily_record(root: Path, valid_time: datetime) -> tuple[Path, int] | None:
    requested = valid_time.replace(tzinfo=None)
    for path in daily_candidates(root, valid_time):
        with Dataset(path) as data:
            if "time" not in data.variables:
                continue
            time_variable = data["time"]
            calendar = (
                time_variable.getncattr("calendar")
                if "calendar" in time_variable.ncattrs()
                else "standard"
            )
            available = num2date(
                time_variable[:],
                time_variable.getncattr("units"),
                calendar=calendar,
                only_use_cftime_datetimes=False,
                only_use_python_datetimes=True,
            )
        for index, value in enumerate(np.asarray(available).reshape(-1)):
            if value.replace(tzinfo=None) == requested:
                return path, index
    return None


def materialize_forcing_hours(
    root: Path, valid_times: list[datetime], destination: Path
) -> list[Path]:
    """Use hourly files when present, otherwise extract exact records from daily archives."""
    ncks = shutil.which("ncks")
    resolved: list[Path] = []
    for valid_time in valid_times:
        hourly = forcing_path(root, valid_time)
        if hourly.is_file():
            resolved.append(hourly)
            continue
        record = find_daily_record(root, valid_time)
        if record is None:
            raise FileNotFoundError(
                f"No hourly file or daily-archive record for {valid_time.isoformat()} below {root}"
            )
        if ncks is None:
            raise RuntimeError("ncks is required to extract forcing records from daily archives")
        archive, index = record
        destination.mkdir(parents=True, exist_ok=True)
        extracted = destination / valid_time.strftime("%Y%m%d%H.LDASIN_DOMAIN1")
        subprocess.run(
            [ncks, "-O", "-d", f"time,{index},{index}", str(archive), str(extracted)],
            check=True,
            capture_output=True,
            text=True,
        )
        with Dataset(extracted, "a") as data:
            data.setncattr("valid_time", valid_time.replace(tzinfo=None).isoformat())
        resolved.append(extracted)
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", required=True, type=date.fromisoformat)
    parser.add_argument("--complete-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--work-directory", type=Path)
    parser.add_argument(
        "--revision", choices=("early", "provisional", "stable"), required=True
    )
    parser.add_argument("--max-iterations", type=int, default=80)
    parser.add_argument("--maximum-unconverged-fraction", type=float, default=0.005)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    layout = OperationalLayout.project_defaults()
    valid_times = prism_window(args.day)
    stamp = args.day.strftime("%Y%m%d")
    prism_month = args.day.strftime("%Y/%m")
    prism_root = settings.prism_data_dir
    minimum = prism_root / "tmin" / prism_month / f"prism_tmin_us_25m_{stamp}.nc"
    maximum = prism_root / "tmax" / prism_month / f"prism_tmax_us_25m_{stamp}.nc"
    precipitation = prism_root / "ppt" / prism_month / f"prism_ppt_us_25m_{stamp}.nc"
    for path in (minimum, maximum, precipitation):
        if not path.is_file():
            raise FileNotFoundError(f"Required PRISM input is missing: {path}")

    work_root = args.work_directory or temporary_work_root(settings, f"prism-final-{stamp}")
    work_root.mkdir(parents=True, exist_ok=True)
    output_directory = args.output_root / args.day.strftime("%Y/%m")
    daily = output_directory / f"{stamp}.LDASIN_DOMAIN1.nc"
    diagnostics = output_directory / f"{stamp}.prism_precipitation_diagnostics.nc"
    if daily.exists() and not args.force:
        raise FileExistsError(f"Output exists; use --force to replace it: {daily}")

    with tempfile.TemporaryDirectory(prefix=f"prism_complete_{stamp}_", dir=work_root) as temp:
        temp = Path(temp)
        hours = materialize_forcing_hours(
            args.complete_root, valid_times, temp / "extracted_hours"
        )
        constraint = temp / f"prism_temperature_constraint.{stamp}.nc"
        corrected = temp / f"hybrid_temperature_prism_corrected.{stamp}.nc"
        create_prism_temperature_constraints(
            minimum,
            maximum,
            settings.data_root / "static/prism/prism_an_4km_elevation.nc",
            layout.target_grid,
            layout.target_elevation,
            settings.data_root / "static/remapping/nwm_conus_1km/prism_bilinear.nc",
            constraint,
            work_directory=temp,
            force=True,
        )
        apply_daily_temperature_constraint(
            hours,
            constraint,
            corrected,
            baseline_variable="T2D",
            force=True,
        )
        command = [
            sys.executable,
            str(settings.project_root / "bin/reconcile_prism_precipitation_day.py"),
            *(str(path) for path in hours),
            "--prism",
            str(precipitation),
            "--weights",
            str(
                settings.data_root
                / "static/remapping/nwm_conus_1km/nwm_to_prism_conservative.nc"
            ),
            "--daily-output",
            str(daily),
            "--day",
            args.day.isoformat(),
            "--temperature-corrected-day",
            str(corrected),
            "--diagnostics",
            str(diagnostics),
            "--revision",
            args.revision,
            "--max-iterations",
            str(args.max_iterations),
            "--maximum-unconverged-fraction",
            str(args.maximum_unconverged_fraction),
            "--work-directory",
            str(temp),
        ]
        if args.force:
            command.append("--force")
        completed = subprocess.run(command, check=False)
        if completed.returncode:
            return completed.returncode
    print(daily, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
