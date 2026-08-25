#!/usr/bin/env python3
#SBATCH --job-name=stage4-backfill
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --time=48:00:00
"""Download, convert, and daily-archive one calendar month of stable Stage-IV."""

from __future__ import annotations

import calendar
import os
import subprocess
import sys
from datetime import date
from pathlib import Path


def add_months(year: int, month: int, offset: int) -> tuple[int, int]:
    index = year * 12 + month - 1 + offset
    return divmod(index, 12)[0], divmod(index, 12)[1] + 1


def run(command: list[str]) -> int:
    return subprocess.run(command, check=False).returncode


def main() -> int:
    start_month = os.environ["HYDRO_OPS_START_MONTH"]
    first_day = date.fromisoformat(os.environ["HYDRO_OPS_FIRST_DAY"])
    final_day = date.fromisoformat(os.environ["HYDRO_OPS_FINAL_DAY"])
    year, month = int(start_month[:4]), int(start_month[4:6])
    year, month = add_months(year, month, int(os.environ["SLURM_ARRAY_TASK_ID"]))
    start = max(date(year, month, 1), first_day)
    end = min(date(year, month, calendar.monthrange(year, month)[1]), final_day)
    if end < start:
        return 0
    python = os.environ.get("HYDRO_OPS_PYTHON", sys.executable)
    project = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"])
    download = [
        python, "-m", "hydro_ops.cli", "download", "stage4", "--stream", "archive",
        "--start", start.isoformat(), "--end", end.isoformat(), "--allow-missing",
    ]
    if run(download):
        return 1
    archive = [
        python, str(project / "bin/archive_forcing_daily.py"), "stage4",
        "--stream", "archive", "--start", start.isoformat(), "--end", end.isoformat(),
        "--delete-hourly", "--allow-incomplete", "--jobs", "32",
    ]
    return run(archive)


if __name__ == "__main__":
    raise SystemExit(main())
