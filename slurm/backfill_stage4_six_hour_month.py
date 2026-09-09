#!/usr/bin/env python3
#SBATCH --job-name=stage4-6h-constraint-backfill
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --time=04:00:00
"""SLURM array entry point: convert one month of six-hour Stage-IV constraints."""

from __future__ import annotations

import calendar
import os
import subprocess
import sys
from datetime import date
from pathlib import Path


def shift_month(value: date, offset: int) -> date:
    month_index = value.year * 12 + value.month - 1 + offset
    return date(month_index // 12, month_index % 12 + 1, 1)


def main() -> int:
    first = date.fromisoformat(os.environ["HYDRO_OPS_STAGE4_6H_START"])
    final = date.fromisoformat(os.environ["HYDRO_OPS_STAGE4_6H_END"])
    month = shift_month(first.replace(day=1), int(os.environ["SLURM_ARRAY_TASK_ID"]))
    start = max(first, month)
    end = min(final, month.replace(day=calendar.monthrange(month.year, month.month)[1]))
    scratch = Path(
        f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}"
    )
    scratch.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["HYDRO_OPS_WORK_ROOT"] = str(scratch)
    return subprocess.run(
        [
            os.environ.get("HYDRO_OPS_PYTHON", sys.executable),
            "bin/backfill_stage4_six_hour.py",
            "--start",
            start.isoformat(),
            "--end",
            end.isoformat(),
        ],
        env=environment,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
