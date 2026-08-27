#!/usr/bin/env python3
"""Consolidate one calendar day of hourly NWM LDASIN files."""

from __future__ import annotations

import argparse
import json
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
    parser.add_argument(
        "--delete-hourly",
        action="store_true",
        help="remove hourly files and manifests only after verified daily publication",
    )
    args = parser.parse_args()

    start = datetime.combine(args.day, datetime.min.time(), tzinfo=UTC)
    paths = [hourly_path(args.hourly_root, start + timedelta(hours=hour)) for hour in range(24)]
    missing = [path for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing {len(missing)} hourly inputs; first is {missing[0]}")
    destination = (
        args.output_root
        / args.day.strftime("%Y/%m")
        / f"{args.day:%Y%m%d}.LDASIN_DOMAIN1"
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
    if args.delete_hourly:
        manifest_path = destination.with_suffix(destination.suffix + ".manifest.json")
        record = json.loads(manifest_path.read_text())
        record["cleanup_state"] = "in_progress"
        partial = manifest_path.with_suffix(manifest_path.suffix + ".part")
        partial.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        partial.replace(manifest_path)
        for path in paths:
            path.unlink()
            path.with_suffix(f"{path.suffix}.manifest.json").unlink(missing_ok=True)
        for directory in sorted({path.parent for path in paths}, reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
        record.update(
            cleanup_state="complete",
            cleanup_time=datetime.now(UTC).isoformat(),
            hourly_sources_removed=True,
        )
        partial.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        partial.replace(manifest_path)
    print(destination, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
