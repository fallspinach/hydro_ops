#!/usr/bin/env python3
"""Submit full scans of hourly forcing days as a bounded SLURM array."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from hydro_ops.config import load_settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--only-days", nargs="+", type=date.fromisoformat)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--report-root", required=True, type=Path)
    parser.add_argument("--max-concurrent", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.end < args.start or args.max_concurrent < 1:
        parser.error("date range and concurrency must be positive")
    days = [
        args.start + timedelta(days=index)
        for index in range((args.end - args.start).days + 1)
    ]
    if args.only_days:
        requested = set(args.only_days)
        if requested.difference(days):
            parser.error("--only-days contains a date outside --start/--end")
        days = [day for day in days if day in requested]
    tasks = [
        {
            "root": str(args.root.resolve()),
            "day": day.isoformat(),
            "report": str((args.report_root / f"{args.scenario}.{day:%Y%m%d}.json").resolve()),
            "scenario": args.scenario,
        }
        for day in days
    ]
    print(f"eligible_validations={len(tasks)}")
    if not tasks:
        return 0
    settings = load_settings()
    settings.work_root.mkdir(parents=True, exist_ok=True)
    settings.log_root.mkdir(parents=True, exist_ok=True)
    args.report_root.mkdir(parents=True, exist_ok=True)
    task_file = settings.work_root / (
        f"hourly-forcing-validation-{datetime.now(UTC):%Y%m%dT%H%M%S%f}.jsonl"
    )
    if not args.dry_run:
        task_file.write_text("".join(json.dumps(task, sort_keys=True) + "\n" for task in tasks))
    command = [
        "sbatch",
        f"--partition={settings.slurm_partition}",
        f"--array=0-{len(tasks) - 1}%{args.max_concurrent}",
        (
            "--export=ALL,"
            f"HYDRO_OPS_PYTHON={sys.executable},"
            f"HYDRO_OPS_HOURLY_VALIDATION_TASK_FILE={task_file.resolve()}"
        ),
        f"--output={settings.log_root}/hourly-forcing-validation-%A_%a.out",
        "slurm/validate_hourly_forcing_day.py",
    ]
    if settings.slurm_account:
        command.insert(2, f"--account={settings.slurm_account}")
    if args.dry_run:
        print(" ".join(command))
        return 0
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
