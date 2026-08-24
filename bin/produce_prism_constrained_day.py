#!/usr/bin/env python3
"""Finalize 24 hybrid hours with PRISM temperature and publish a daily LDASIN file."""

from __future__ import annotations

import argparse
import concurrent.futures
import tempfile
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from netCDF4 import Dataset

from hydro_ops.config import load_settings
from hydro_ops.forcing.assemble import add_precipitation_to_ldasin
from hydro_ops.forcing.daily_archive import create_daily_archive
from hydro_ops.forcing.operations import OperationalLayout, discover_precipitation_candidates
from hydro_ops.forcing.precipitation_hour import process_precipitation_hour
from hydro_ops.forcing.prism_temperature import (
    apply_constrained_temperature_hour,
    apply_daily_temperature_constraint,
    create_prism_temperature_constraints,
)
from hydro_ops.work import temporary_work_root


def prism_window(day: date) -> list[datetime]:
    start = datetime.combine(day - timedelta(days=1), time(12), tzinfo=UTC)
    return [start + timedelta(hours=hour) for hour in range(24)]


def _complete_hour_path(root: Path, valid_time: datetime) -> Path:
    return root / valid_time.strftime("%Y/%m/%d/%Y%m%d%H.LDASIN_DOMAIN1")


def _finish_hour(arguments: tuple[datetime, Path, Path, Path, Path, Path | None]) -> Path:
    valid_time, preliminary, corrected_day, output_root, work_root, complete_root = arguments
    layout = OperationalLayout.project_defaults()
    output = _complete_hour_path(output_root, valid_time)
    output.parent.mkdir(parents=True, exist_ok=True)
    if complete_root is not None:
        complete = _complete_hour_path(complete_root, valid_time)
        with Dataset(complete) as source:
            if "RAINRATE" not in source.variables:
                raise ValueError(f"Complete forcing input has no RAINRATE: {complete}")
        apply_constrained_temperature_hour(
            complete, corrected_day, output, valid_time, force=True
        )
        return output
    with tempfile.TemporaryDirectory(prefix="prism_hour_", dir=work_root) as temporary:
        temporary = Path(temporary)
        constrained = temporary / "seven_constrained.LDASIN_DOMAIN1"
        precipitation = temporary / "precipitation.nc"
        apply_constrained_temperature_hour(
            preliminary, corrected_day, constrained, valid_time, force=True
        )
        candidates, quality = discover_precipitation_candidates(valid_time, layout)
        if not candidates:
            raise FileNotFoundError(f"No precipitation candidates for {valid_time.isoformat()}")
        weights = {
            name: (
                layout.mrms_conservative
                if name.startswith("mrms_")
                else layout.stage4_conservative
                if name.startswith("stage4_")
                else layout.nldas2_conservative
                if name == "nldas2"
                else layout.hrrr_conservative
            )
            for name in candidates
        }
        process_precipitation_hour(
            candidates,
            weights,
            layout.target_grid,
            precipitation,
            valid_time=valid_time,
            remap_grid_path=layout.remap_grid,
            quality_path=quality,
            quality_weights=layout.mrms_quality_bilinear if quality else None,
            work_directory=temporary,
        )
        add_precipitation_to_ldasin(constrained, precipitation, output, force=True)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", required=True, type=date.fromisoformat)
    parser.add_argument("--preliminary-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument(
        "--complete-root",
        type=Path,
        help=(
            "reuse complete hourly LDASIN files below this root and preserve precipitation; "
            "without this option precipitation is produced from source candidates"
        ),
    )
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    settings = load_settings()
    layout = OperationalLayout.project_defaults()
    valid_times = prism_window(args.day)
    preliminary = [
        args.preliminary_root / f"{valid:%Y%m%d%H}.LDASIN_DOMAIN1"
        for valid in valid_times
    ]
    missing = [path for path in preliminary if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing {len(missing)} preliminary hybrid hours; first: {missing[0]}")
    if args.complete_root is not None:
        missing_complete = [
            _complete_hour_path(args.complete_root, valid)
            for valid in valid_times
            if not _complete_hour_path(args.complete_root, valid).is_file()
        ]
        if missing_complete:
            raise FileNotFoundError(
                f"Missing {len(missing_complete)} complete forcing hours; "
                f"first: {missing_complete[0]}"
            )
    stamp = args.day.strftime("%Y%m%d")
    prism_root = settings.prism_data_dir
    minimum = prism_root / "tmin" / args.day.strftime("%Y/%m") / f"prism_tmin_us_25m_{stamp}.nc"
    maximum = prism_root / "tmax" / args.day.strftime("%Y/%m") / f"prism_tmax_us_25m_{stamp}.nc"
    work_root = temporary_work_root(settings, f"prism-day-{stamp}")
    constraint = args.preliminary_root / f"prism_temperature_constraint.{stamp}.nc"
    corrected = args.preliminary_root / f"hybrid_temperature_prism_corrected.{stamp}.nc"
    create_prism_temperature_constraints(
        minimum,
        maximum,
        settings.data_root / "static/prism/prism_an_4km_elevation.nc",
        layout.target_grid,
        layout.target_elevation,
        settings.data_root / "static/remapping/nwm_conus_1km/prism_bilinear.nc",
        constraint,
        work_directory=work_root,
        force=args.force,
    )
    apply_daily_temperature_constraint(
        preliminary,
        constraint,
        corrected,
        baseline_variable="T2D",
        force=args.force,
    )
    tasks = [
        (valid, path, corrected, args.output_root, work_root, args.complete_root)
        for valid, path in zip(valid_times, preliminary, strict=True)
    ]
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.jobs) as executor:
        hourly = list(executor.map(_finish_hour, tasks))
    daily = (
        args.output_root / args.day.strftime("%Y/%m") /
        f"{stamp}.LDASIN_DOMAIN1.nc"
    )
    create_daily_archive(
        hourly,
        daily,
        args.day,
        compression_level=2,
        work_directory=work_root,
    )
    with Dataset(daily, "a") as output:
        output.setncattr("prism_day", args.day.isoformat())
        output.setncattr("forcing_window", "[D-1 12:00 UTC, D 12:00 UTC)")
        output.setncattr("prism_temperature_constraint", str(constraint))
        output.setncattr(
            "precipitation_processing",
            "reused_complete_hour" if args.complete_root is not None else "source_recomposite",
        )
    print(daily, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
