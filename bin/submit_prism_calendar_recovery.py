#!/usr/bin/env python3
"""Submit bounded recovery of quarantined PRISM windows as UTC calendar days."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from netCDF4 import Dataset, num2date

from hydro_ops.config import load_settings
from hydro_ops.forcing.streams import validate_stream_output_root


def window_dates(root: Path) -> set[date]:
    dates: set[date] = set()
    for path in root.glob("*/*/*.LDASIN_DOMAIN1"):
        try:
            label = date.fromisoformat(f"{path.name[:4]}-{path.name[4:6]}-{path.name[6:8]}")
            with Dataset(path) as data:
                accepted = str(getattr(data, "prism_reconciliation_accepted", "false")).lower()
            if accepted == "true":
                dates.add(label)
        except (OSError, ValueError):
            continue
    return dates


def valid_calendar_output(path: Path, day: date) -> bool:
    if not path.is_file():
        return False
    try:
        with Dataset(path) as data:
            time = data["time"]
            values = num2date(
                time[:],
                time.getncattr("units"),
                calendar=time.getncattr("calendar") if "calendar" in time.ncattrs() else "standard",
                only_use_cftime_datetimes=False,
                only_use_python_datetimes=True,
            )
            return (
                len(values) == 24
                and values[0].date() == day
                and values[-1].date() == day
                and str(getattr(data, "archive_granularity", "")) == "utc_calendar_day"
                and str(getattr(data, "prism_reconciliation_accepted", "false")).lower()
                == "true"
            )
    except (OSError, KeyError, AttributeError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--stream", required=True, choices=("nrt", "retro"))
    parser.add_argument("--max-concurrent", type=int, default=4)
    parser.add_argument(
        "--cpus-per-task",
        type=int,
        default=12,
        help="CPUs reserved per worker; this cluster allocates memory primarily per CPU",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    validate_stream_output_root(args.output_root, args.stream)
    windows = window_dates(args.input_root)
    days = sorted(day for day in windows if day + timedelta(days=1) in windows)
    days = [
        day
        for day in days
        if not valid_calendar_output(
            args.output_root / day.strftime("%Y/%m") / f"{day:%Y%m%d}.LDASIN_DOMAIN1", day
        )
    ]
    print(json.dumps({"stream": args.stream, "eligible_calendar_days": len(days)}, indent=2))
    if not days:
        return 0
    settings = load_settings()
    settings.work_root.mkdir(parents=True, exist_ok=True)
    settings.log_root.mkdir(parents=True, exist_ok=True)
    task_file = settings.work_root / (
        f"prism-calendar-recovery-{args.stream}-{datetime.now(UTC):%Y%m%dT%H%M%S%f}.jsonl"
    )
    tasks = [
        {
            "day": day.isoformat(),
            "input_root": str(args.input_root.resolve()),
            "output_root": str(args.output_root.resolve()),
            "stream": args.stream,
        }
        for day in days
    ]
    if not args.dry_run:
        task_file.write_text("".join(json.dumps(task, sort_keys=True) + "\n" for task in tasks))
    command = [
        "sbatch",
        f"--partition={settings.slurm_partition}",
        f"--array=0-{len(tasks) - 1}%{args.max_concurrent}",
        f"--job-name=prism-{args.stream}-calendar-recovery",
        f"--cpus-per-task={args.cpus_per_task}",
        "--tmp=120000",
        (
            "--export=ALL,"
            f"HYDRO_OPS_PYTHON={sys.executable},"
            f"HYDRO_OPS_PROJECT_ROOT={settings.project_root},"
            f"HYDRO_OPS_PRISM_RECOMBINE_TASK_FILE={task_file.resolve()}"
        ),
        f"--output={settings.log_root}/prism-calendar-recovery-%A_%a.out",
        "slurm/recombine_prism_calendar_day.py",
    ]
    if settings.slurm_account:
        command.insert(2, f"--account={settings.slurm_account}")
    if args.dry_run:
        print(" ".join(command))
        return 0
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
