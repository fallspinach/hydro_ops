#!/usr/bin/env python3
#SBATCH --job-name=nldas2-backfill
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=48:00:00
"""Download and immediately archive one calendar month of NLDAS-2."""

from __future__ import annotations

import calendar
import os
import subprocess
import sys
from datetime import date


def add_months(year: int, month: int, offset: int) -> tuple[int, int]:
    index = year * 12 + month - 1 + offset
    return divmod(index, 12)[0], divmod(index, 12)[1] + 1


def run(command: list[str]) -> int:
    return subprocess.run(command, check=False).returncode


def main() -> int:
    start_month = os.environ["HYDRO_OPS_START_MONTH"]
    year, month = int(start_month[:4]), int(start_month[4:6])
    year, month = add_months(year, month, int(os.environ["SLURM_ARRAY_TASK_ID"]))
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    python = os.environ.get("HYDRO_OPS_PYTHON", sys.executable)
    download = [
        python,
        "-m",
        "hydro_ops.cli",
        "download",
        "nldas2",
        "--start",
        start.isoformat(),
        "--end",
        end.isoformat(),
    ]
    if run(download):
        return 1
    archive = [
        python,
        "bin/archive_forcing_daily.py",
        "nldas2",
        "--start",
        start.isoformat(),
        "--end",
        end.isoformat(),
        "--jobs",
        os.environ.get("SLURM_CPUS_PER_TASK", "1"),
        "--delete-hourly",
        "--minimum-age-days",
        "31",
    ]
    return run(archive)


if __name__ == "__main__":
    raise SystemExit(main())
