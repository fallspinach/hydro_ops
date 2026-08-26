#!/usr/bin/env python3
"""Consolidate one calendar day of hourly NWM LDASIN files."""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from hydro_ops.config import load_settings
from hydro_ops.forcing.daily_archive import create_daily_archive
from hydro_ops.work import temporary_work_root


def hourly_path(root: Path, valid_time: datetime) -> Path:
    flat = root / valid_time.strftime("%Y%m%d%H.LDASIN_DOMAIN1")
    if flat.is_file():
        return flat
    return root / valid_time.strftime("%Y/%m/%d/%Y%m%d%H.LDASIN_DOMAIN1")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", required=True, type=date.fromisoformat)
    parser.add_argument("--hourly-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--work-directory", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    start = datetime.combine(args.day, datetime.min.time(), tzinfo=UTC)
    paths = [hourly_path(args.hourly_root, start + timedelta(hours=hour)) for hour in range(24)]
    missing = [path for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing {len(missing)} hourly inputs; first is {missing[0]}")
    destination = (
        args.output_root
        / args.day.strftime("%Y/%m")
        / f"{args.day:%Y%m%d}.LDASIN_DOMAIN1.nc"
    )
    if destination.exists() and not args.force:
        raise FileExistsError(f"Output exists; use --force to replace it: {destination}")
    settings = load_settings()
    work = args.work_directory or temporary_work_root(settings, f"nwm-daily-{args.day:%Y%m%d}")
    create_daily_archive(
        paths,
        destination,
        args.day,
        compression_level=2,
        work_directory=work,
        verification="targeted",
    )
    print(destination, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
