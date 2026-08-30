#!/usr/bin/env python3
"""Submit a bounded array of historical monthly-PRISM forcing tasks."""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from hydro_ops.config import load_settings
from hydro_ops.forcing.streams import forcing_stream_root, validate_stream_output_root


def parse_month(value: str) -> date:
    try:
        parsed = date.fromisoformat(f"{value}-01")
    except ValueError as error:
        raise argparse.ArgumentTypeError("month must be YYYY-MM") from error
    return parsed


def months_between(start: date, end: date) -> list[tuple[int, int]]:
    if end < start:
        raise ValueError("end month precedes start month")
    values = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        values.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=parse_month)
    parser.add_argument("--end", required=True, type=parse_month)
    parser.add_argument("--complete-root", required=True, type=Path)
    parser.add_argument(
        "--output-root", type=Path, help="defaults to outputs/forcing/nwm/retro"
    )
    parser.add_argument("--precipitation-weights", required=True, type=Path)
    parser.add_argument("--maximum-ratio", type=float, required=True)
    parser.add_argument("--maximum-corrected-hourly-depth", type=float, default=300.0)
    parser.add_argument("--max-concurrent", type=int, default=2)
    parser.add_argument("--cpus-per-task", type=int, default=64)
    parser.add_argument("--time", default="08:00:00")
    parser.add_argument(
        "--dependency",
        help="SLURM dependency expression, for example afterok:123:456",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        months = months_between(args.start, args.end)
    except ValueError as error:
        parser.error(str(error))
    if args.max_concurrent < 1 or args.cpus_per_task < 1 or args.maximum_ratio < 1:
        parser.error("concurrency, CPUs, and maximum ratio must be positive")
    settings = load_settings()
    output_root = validate_stream_output_root(
        args.output_root or forcing_stream_root(settings.project_root, "retro"), "retro"
    )
    settings.work_root.mkdir(parents=True, exist_ok=True)
    settings.log_root.mkdir(parents=True, exist_ok=True)
    task_file = settings.work_root / (f"prism-month-tasks-{datetime.now(UTC):%Y%m%dT%H%M%S%f}.txt")
    if not args.dry_run:
        task_file.write_text("".join(f"{year} {month}\n" for year, month in months))
    exports = [
        "ALL",
        f"HYDRO_OPS_PYTHON={sys.executable}",
        f"HYDRO_OPS_COMPLETE_ROOT={args.complete_root.resolve()}",
        f"HYDRO_OPS_OUTPUT_ROOT={output_root}",
        "HYDRO_OPS_FORCING_STREAM=retro",
        f"HYDRO_OPS_MONTH_TASK_FILE={task_file.resolve()}",
        f"HYDRO_OPS_PRECIPITATION_WEIGHTS={args.precipitation_weights.resolve()}",
        f"HYDRO_OPS_MAXIMUM_RATIO={args.maximum_ratio}",
        (
            "HYDRO_OPS_MAXIMUM_CORRECTED_HOURLY_DEPTH="
            f"{args.maximum_corrected_hourly_depth}"
        ),
    ]
    if args.force:
        exports.append("HYDRO_OPS_FORCE=1")
    command = [
        "sbatch",
        f"--partition={settings.slurm_partition}",
        "--job-name=prism-historical-month",
        f"--array=0-{len(months) - 1}%{args.max_concurrent}",
        f"--cpus-per-task={args.cpus_per_task}",
        f"--time={args.time}",
        f"--export={','.join(exports)}",
        f"--output={settings.log_root}/prism-historical-month-%A_%a.out",
        "slurm/produce_prism_constrained_month.py",
    ]
    if args.dependency:
        command.insert(2, f"--dependency={args.dependency}")
    if settings.slurm_account:
        command.insert(2, f"--account={settings.slurm_account}")
    print(f"months={len(months)} start={args.start:%Y-%m} end={args.end:%Y-%m}")
    if args.dry_run:
        print(" ".join(command))
        return 0
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
