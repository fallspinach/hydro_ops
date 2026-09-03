#!/usr/bin/env python3
"""Submit PRISM constraints that publish only UTC calendar-day forcing files."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from netCDF4 import Dataset, num2date

from hydro_ops.config import load_settings
from hydro_ops.forcing.streams import (
    baseline_root,
    forcing_stream_root,
    validate_stream_output_root,
)

PRISM_STABLE_AGE_DAYS = 183


def revision_for_day(day: date, today: date, stream: str) -> str | None:
    age = (today - day).days
    if stream == "retro":
        return "stable" if age >= PRISM_STABLE_AGE_DAYS else None
    if age >= PRISM_STABLE_AGE_DAYS:
        return None
    return "early" if (day.year, day.month) == (today.year, today.month) else "provisional"


def valid_output(path: Path, day: date) -> bool:
    try:
        with Dataset(path) as data:
            time = data["time"]
            values = num2date(
                time[:],
                time.units,
                calendar=getattr(time, "calendar", "standard"),
                only_use_cftime_datetimes=False,
                only_use_python_datetimes=True,
            )
            return (
                len(values) == 24
                and values[0].date() == day
                and values[-1].date() == day
                and str(getattr(data, "archive_granularity", "")) == "utc_calendar_day"
                and str(getattr(data, "prism_reconciliation_accepted", "false")).lower() == "true"
            )
    except (OSError, KeyError, AttributeError):
        return False


def contiguous_batches(days: list[tuple[date, str]], maximum: int) -> list[tuple[date, date, str]]:
    batches: list[tuple[date, date, str]] = []
    for day, revision in days:
        if (
            not batches
            or day != batches[-1][1] + timedelta(days=1)
            or revision != batches[-1][2]
            or (day - batches[-1][0]).days >= maximum
        ):
            batches.append((day, day, revision))
        else:
            batches[-1] = (batches[-1][0], day, revision)
    return batches


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--stream", required=True, choices=("nrt", "retro"))
    parser.add_argument("--baseline-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--batch-days", type=int, default=31)
    parser.add_argument("--max-concurrent", type=int, default=4)
    parser.add_argument("--cpus-per-task", type=int, default=64)
    parser.add_argument("--tmp-mb", type=int, default=120000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if (
        args.end < args.start
        or min(args.batch_days, args.max_concurrent, args.cpus_per_task, args.tmp_mb) < 1
    ):
        parser.error("invalid date range or non-positive resource setting")
    settings = load_settings()
    baseline = (args.baseline_root or baseline_root(settings.project_root)).resolve()
    output = validate_stream_output_root(
        args.output_root or forcing_stream_root(settings.project_root, args.stream), args.stream
    )
    today = datetime.now(UTC).date()
    days: list[tuple[date, str]] = []
    blocked_without_previous_day: list[date] = []
    blocked_without_next_day: list[date] = []
    blocked_without_prism: list[date] = []
    day = args.start
    while day <= args.end:
        baseline_path = baseline / day.strftime("%Y/%m") / f"{day:%Y%m%d}.LDASIN_DOMAIN1"
        previous_day = day - timedelta(days=1)
        previous_baseline = (
            baseline / previous_day.strftime("%Y/%m") / f"{previous_day:%Y%m%d}.LDASIN_DOMAIN1"
        )
        next_day = day + timedelta(days=1)
        next_baseline = baseline / next_day.strftime("%Y/%m") / f"{next_day:%Y%m%d}.LDASIN_DOMAIN1"
        destination = output / day.strftime("%Y/%m") / f"{day:%Y%m%d}.LDASIN_DOMAIN1"
        revision = revision_for_day(day, today, args.stream)
        if revision and baseline_path.is_file() and not valid_output(destination, day):
            prism_days = (day, next_day)
            prism_ready = all(
                (
                    settings.prism_data_dir
                    / variable
                    / prism_day.strftime("%Y/%m")
                    / f"prism_{variable}_us_25m_{prism_day:%Y%m%d}.nc"
                ).is_file()
                for prism_day in prism_days
                for variable in ("ppt", "tmin", "tmax")
            )
            if not prism_ready:
                blocked_without_prism.append(day)
            elif not previous_baseline.is_file():
                blocked_without_previous_day.append(day)
            elif next_baseline.is_file():
                days.append((day, revision))
            else:
                blocked_without_next_day.append(day)
        day += timedelta(days=1)
    batches = contiguous_batches(days, args.batch_days)
    print(
        json.dumps(
            {
                "eligible_days": len(days),
                "batches": len(batches),
                "first": days[0][0].isoformat() if days else None,
                "last": days[-1][0].isoformat() if days else None,
                "blocked_without_previous_baseline_day": [
                    item.isoformat() for item in blocked_without_previous_day
                ],
                "blocked_without_next_baseline_day": [
                    item.isoformat() for item in blocked_without_next_day
                ],
                "blocked_without_prism": [item.isoformat() for item in blocked_without_prism],
            },
            indent=2,
        )
    )
    if not batches:
        return 0
    task_file = (
        settings.work_root
        / f"prism-calendar-{args.stream}-{datetime.now(UTC):%Y%m%dT%H%M%S%f}.jsonl"
    )
    tasks = [
        {
            "start": first.isoformat(),
            "end": last.isoformat(),
            "stream": args.stream,
            "revision": revision,
            "baseline_root": str(baseline),
            "output_root": str(output),
        }
        for first, last, revision in batches
    ]
    if not args.dry_run:
        task_file.write_text("".join(json.dumps(task, sort_keys=True) + "\n" for task in tasks))
    command = [
        "sbatch",
        f"--partition={settings.slurm_partition}",
        f"--array=0-{len(tasks) - 1}%{args.max_concurrent}",
        f"--job-name=prism-{args.stream}-calendar-batches-{args.start:%Y%m%d}-{args.end:%Y%m%d}",
        f"--cpus-per-task={args.cpus_per_task}",
        f"--tmp={args.tmp_mb}",
        "--time=48:00:00",
        (
            "--export=ALL,"
            f"HYDRO_OPS_PYTHON={sys.executable},"
            f"HYDRO_OPS_PROJECT_ROOT={settings.project_root},"
            f"HYDRO_OPS_PRISM_CALENDAR_TASK_FILE={task_file.resolve()}"
        ),
        f"--output={settings.log_root}/prism-calendar-batch-%A_%a.out",
        "slurm/produce_prism_calendar_batch.py",
    ]
    if settings.slurm_account:
        command.insert(2, f"--account={settings.slurm_account}")
    if args.dry_run:
        print(" ".join(command))
        return 0
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
