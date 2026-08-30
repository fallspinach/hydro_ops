#!/usr/bin/env python3
"""Submit missing or stale PRISM-constrained daily forcing as a bounded SLURM array."""

from __future__ import annotations

import argparse
import getpass
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


def revision_for_day(day: date, today: date) -> str:
    if day.year == today.year and day.month == today.month:
        return "early"
    if (today - day).days >= PRISM_STABLE_AGE_DAYS:
        return "stable"
    return "provisional"


def revision_for_stream(day: date, today: date, stream: str) -> str | None:
    """Return the eligible PRISM revision for an independently retained stream."""
    revision = revision_for_day(day, today)
    if stream == "nrt":
        return revision if revision in {"early", "provisional"} else None
    if stream == "retro":
        return revision if revision == "stable" else None
    raise ValueError(f"Unknown forcing stream: {stream}")


def scan_window(
    today: date, stream: str, lookback_days: int, prism_lag_days: int
) -> tuple[date, date]:
    """Return the inclusive stream-specific scheduler window."""
    end_age = prism_lag_days if stream == "nrt" else PRISM_STABLE_AGE_DAYS
    end = today - timedelta(days=end_age)
    return end - timedelta(days=lookback_days - 1), end


def prism_inputs(settings, day: date) -> tuple[Path, Path, Path]:
    root = settings.prism_data_dir
    month = day.strftime("%Y/%m")
    stamp = day.strftime("%Y%m%d")
    return tuple(
        root / variable / month / f"prism_{variable}_us_25m_{stamp}.nc"
        for variable in ("ppt", "tmin", "tmax")
    )


def output_path(root: Path, day: date) -> Path:
    return root / day.strftime("%Y/%m") / f"{day:%Y%m%d}.LDASIN_DOMAIN1"


def output_revision(path: Path) -> str | None:
    try:
        with Dataset(path) as data:
            return data.getncattr("prism_precipitation_revision")
    except (OSError, AttributeError):
        return None


def baseline_sources(root: Path, day: date) -> list[Path] | None:
    start = datetime.combine(day - timedelta(days=1), datetime.min.time(), tzinfo=UTC) + timedelta(
        hours=12
    )
    sources: set[Path] = set()
    for index in range(24):
        valid = start + timedelta(hours=index)
        hourly = root / valid.strftime("%Y%m%d%H.LDASIN_DOMAIN1")
        hierarchy = root / valid.strftime("%Y/%m/%d/%Y%m%d%H.LDASIN_DOMAIN1")
        if hourly.is_file() or hierarchy.is_file():
            sources.add(hourly if hourly.is_file() else hierarchy)
            continue
        daily: list[Path] = []
        for offset in (-1, 0, 1):
            label = (valid + timedelta(days=offset)).date()
            stamp = label.strftime("%Y%m%d")
            daily.extend(
                (
                    root / label.strftime("%Y/%m") / f"{stamp}.LDASIN_DOMAIN1",
                    root / label.strftime("%Y/%m") / f"{stamp}.LDASIN_DOMAIN1.nc",
                    root / label.strftime("%Y") / f"{stamp}.LDASIN_DOMAIN1",
                    root / label.strftime("%Y") / f"{stamp}.LDASIN_DOMAIN1.nc",
                    root / label.strftime("%Y/%m/%d") / f"{stamp}.LDASIN_DOMAIN1",
                    root / label.strftime("%Y/%m/%d") / f"{stamp}.LDASIN_DOMAIN1.nc",
                )
            )
        existing = next(
            (path for path in daily if path.is_file() and daily_contains(path, valid)), None
        )
        if existing is None:
            return None
        sources.add(existing)
    return sorted(sources)


def daily_contains(path: Path, valid_time: datetime) -> bool:
    try:
        with Dataset(path) as data:
            time_variable = data["time"]
            calendar = (
                time_variable.getncattr("calendar")
                if "calendar" in time_variable.ncattrs()
                else "standard"
            )
            values = num2date(
                time_variable[:],
                time_variable.getncattr("units"),
                calendar=calendar,
                only_use_cftime_datetimes=False,
                only_use_python_datetimes=True,
            )
        requested = valid_time.replace(tzinfo=None)
        return any(value.replace(tzinfo=None) == requested for value in values)
    except (OSError, KeyError, AttributeError):
        return False


def needs_update(
    settings, complete_root: Path, constrained_root: Path, day: date, revision: str
) -> bool:
    inputs = prism_inputs(settings, day)
    if not all(path.is_file() and path.stat().st_size > 0 for path in inputs):
        return False
    baseline = baseline_sources(complete_root, day)
    if baseline is None:
        return False
    output = output_path(constrained_root, day)
    legacy_output = output.with_suffix(f"{output.suffix}.nc")
    if not output.is_file() and legacy_output.is_file():
        output = legacy_output
    manifest = output.with_suffix(output.suffix + ".manifest.json")
    if not output.is_file() or not manifest.is_file() or output_revision(output) != revision:
        return True
    return any(path.stat().st_mtime > output.stat().st_mtime for path in (*inputs, *baseline))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--complete-root", type=Path)
    parser.add_argument(
        "--output-root", type=Path, help="defaults to outputs/forcing/nwm/STREAM"
    )
    parser.add_argument("--stream", required=True, choices=("nrt", "retro"))
    parser.add_argument("--lookback-days", type=int)
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--max-concurrent", type=int, default=4)
    parser.add_argument("--cpus-per-task", type=int, default=64)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    output_root = validate_stream_output_root(
        args.output_root or forcing_stream_root(settings.project_root, args.stream), args.stream
    )
    complete_root = (args.complete_root or baseline_root(settings.project_root)).resolve()
    lookback = args.lookback_days or settings.prism_refresh_days
    if lookback < 1 or args.max_concurrent < 1 or args.cpus_per_task < 1:
        parser.error("lookback, concurrency, and CPUs must be positive")
    if (args.start is None) != (args.end is None):
        parser.error("--start and --end must be supplied together")
    today = datetime.now(UTC).date()
    if args.start is not None and args.end is not None:
        if args.end < args.start:
            parser.error("--end must not precede --start")
        start, end = args.start, args.end
        days = [start + timedelta(days=index) for index in range((end - start).days + 1)]
    else:
        start, end = scan_window(today, args.stream, lookback, settings.prism_lag_days)
        days = [start + timedelta(days=index) for index in range(lookback)]
    candidates = [
        (day, revision)
        for day in days
        if (revision := revision_for_stream(day, today, args.stream)) is not None
    ]
    tasks = [
        (day, revision)
        for day, revision in candidates
        if needs_update(settings, complete_root, output_root, day, revision)
    ]
    summary = {
        "stream": args.stream,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "eligible_updates": len(tasks),
        "tasks": [{"day": day.isoformat(), "revision": revision} for day, revision in tasks],
    }
    print(json.dumps(summary, indent=2))
    if not tasks:
        return 0
    if not args.force:
        active = subprocess.run(
            [
                "squeue",
                "--noheader",
                "--user",
                getpass.getuser(),
                "--name",
                f"prism-{args.stream}-day",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        if active.stdout.strip():
            print(f"SKIP active prism-{args.stream}-day job")
            return 0
    settings.work_root.mkdir(parents=True, exist_ok=True)
    settings.log_root.mkdir(parents=True, exist_ok=True)
    task_file = (
        settings.work_root / f"prism-{args.stream}-tasks-{datetime.now(UTC):%Y%m%dT%H%M%S}.txt"
    )
    if not args.dry_run:
        task_file.write_text("".join(f"{day.isoformat()} {revision}\n" for day, revision in tasks))
    command = [
        "sbatch",
        f"--partition={settings.slurm_partition}",
        f"--job-name=prism-{args.stream}-day",
        f"--array=0-{len(tasks) - 1}%{args.max_concurrent}",
        f"--cpus-per-task={args.cpus_per_task}",
        (
            "--export=ALL,"
            f"HYDRO_OPS_PYTHON={sys.executable},"
            f"HYDRO_OPS_PROJECT_ROOT={settings.project_root},"
            f"HYDRO_OPS_COMPLETE_ROOT={complete_root},"
            f"HYDRO_OPS_OUTPUT_ROOT={output_root},"
            f"HYDRO_OPS_FORCING_STREAM={args.stream},"
            f"HYDRO_OPS_PRISM_TASK_FILE={task_file.resolve()}"
        ),
        f"--output={settings.log_root}/prism-{args.stream}-%A_%a.out",
        "slurm/produce_prism_constrained_daily.py",
    ]
    if settings.slurm_account:
        command.insert(2, f"--account={settings.slurm_account}")
    weights = (
        settings.data_root / "static/remapping/nwm_conus_1km/nwm_to_prism_conservative_masked.nc"
    )
    command[5] += f",HYDRO_OPS_PRECIPITATION_WEIGHTS={weights.resolve()}"
    if args.dry_run:
        print(" ".join(command))
        return 0
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
