#!/usr/bin/env python3
"""Submit an inclusive range of daily-batched forcing-production tasks."""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from hydro_ops.config import load_settings
from hydro_ops.forcing.complete_day import utc_hours


def complete_day(root: Path, day: date) -> bool:
    for valid in utc_hours(day):
        output = root / valid.strftime("%Y/%m/%d/%Y%m%d%H.LDASIN_DOMAIN1")
        manifest = output.with_suffix(f"{output.suffix}.manifest.json")
        if not output.is_file() or output.stat().st_size == 0 or not manifest.is_file():
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--max-concurrent", type=int, default=16)
    parser.add_argument("--cpus-per-task", type=int, default=12)
    parser.add_argument(
        "--output-root", type=Path, default=Path("outputs/forcing/nwm")
    )
    parser.add_argument(
        "--layout-root",
        type=Path,
        default=Path("."),
        help="project-shaped root containing the data view used for source discovery",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--only-days",
        nargs="+",
        type=date.fromisoformat,
        help="within the inclusive range, submit only these explicit dates",
    )
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="submit only days that do not have 24 complete validated hours",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.end < args.start:
        parser.error("--end must not precede --start")
    if args.max_concurrent <= 0 or args.cpus_per_task < 12:
        parser.error("concurrency must be positive and each task requires at least 12 CPUs")

    days = [
        args.start + timedelta(days=index)
        for index in range((args.end - args.start).days + 1)
    ]
    if args.only_days:
        requested = set(args.only_days)
        outside = requested.difference(days)
        if outside:
            parser.error("--only-days contains a date outside --start/--end")
        days = [day for day in days if day in requested]
    if args.missing_only:
        days = [day for day in days if not complete_day(args.output_root, day)]
    tasks = len(days)
    print(f"eligible_days={tasks}")
    if not tasks:
        return 0
    settings = load_settings()
    settings.log_root.mkdir(parents=True, exist_ok=True)
    task_file = settings.work_root / (
        f"forcing-day-tasks-{datetime.now(UTC):%Y%m%dT%H%M%S%f}.txt"
    )
    settings.work_root.mkdir(parents=True, exist_ok=True)
    if not args.dry_run:
        task_file.write_text("".join(f"{day.isoformat()}\n" for day in days))
    exports = [
        "ALL",
        f"HYDRO_OPS_START_DAY={args.start.isoformat()}",
        f"HYDRO_OPS_PYTHON={sys.executable}",
        f"HYDRO_OPS_OUTPUT_ROOT={args.output_root.resolve()}",
        f"HYDRO_OPS_LAYOUT_ROOT={args.layout_root.resolve()}",
        f"HYDRO_OPS_FORCING_DAY_TASK_FILE={task_file.resolve()}",
    ]
    if args.force:
        exports.append("HYDRO_OPS_FORCE=1")
    command = [
        "sbatch",
        f"--partition={settings.slurm_partition}",
        f"--array=0-{tasks - 1}%{args.max_concurrent}",
        f"--cpus-per-task={args.cpus_per_task}",
        f"--export={','.join(exports)}",
        f"--output={settings.log_root}/forcing-day-%A_%a.out",
        "slurm/produce_forcing_day.py",
    ]
    if settings.slurm_account:
        command.insert(2, f"--account={settings.slurm_account}")
    if args.dry_run:
        print(" ".join(command))
        return 0
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
