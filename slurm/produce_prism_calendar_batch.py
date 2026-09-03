#!/usr/bin/env python3
# SBATCH --nodes=1
# SBATCH --ntasks=1
# SBATCH --time=48:00:00
"""Produce a contiguous batch of PRISM-constrained UTC calendar days."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from netCDF4 import Dataset, num2date


def run(command: list[str], project: Path) -> None:
    completed = subprocess.run(command, cwd=project, check=False)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def validate_publication(path: Path, day: date) -> None:
    """Fail closed before allowing stable baseline retention cleanup."""
    with Dataset(path) as data:
        time = data["time"]
        values = num2date(
            time[:],
            time.units,
            calendar=getattr(time, "calendar", "standard"),
            only_use_cftime_datetimes=False,
            only_use_python_datetimes=True,
        )
        valid = (
            len(values) == 24
            and values[0].date() == day
            and values[-1].date() == day
            and str(getattr(data, "archive_granularity", "")) == "utc_calendar_day"
            and str(getattr(data, "prism_reconciliation_accepted", "false")).lower()
            == "true"
        )
    if not valid:
        raise ValueError(f"Refusing baseline cleanup after invalid publication: {path}")


def main() -> int:
    project = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"])
    python = os.environ.get("HYDRO_OPS_PYTHON", sys.executable)
    task_file = Path(os.environ["HYDRO_OPS_PRISM_CALENDAR_TASK_FILE"])
    task = json.loads(task_file.read_text().splitlines()[int(os.environ["SLURM_ARRAY_TASK_ID"])])
    start = date.fromisoformat(task["start"])
    end = date.fromisoformat(task["end"])
    stream = task["stream"]
    revision = task["revision"]
    baseline = Path(task["baseline_root"])
    destination = Path(task["output_root"])
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}")
    staging = scratch / "prism_windows" / stream
    staging.mkdir(parents=True, exist_ok=True)

    def produce_window(day: date) -> None:
        run(
            [
                python,
                str(project / "bin/produce_prism_constrained_daily.py"),
                "--day",
                day.isoformat(),
                "--complete-root",
                str(baseline),
                "--output-root",
                str(staging),
                "--revision",
                revision,
                "--stream",
                stream,
                "--work-directory",
                str(scratch),
                "--archive-access",
                "direct",
                "--allow-legacy-12utc-output",
            ],
            project,
        )

    produce_window(start)
    day = start
    while day <= end:
        produce_window(day + timedelta(days=1))
        run(
            [
                python,
                str(project / "bin/materialize_calendar_forcing.py"),
                "--input-root",
                str(staging),
                "--output-root",
                str(destination),
                "--start",
                day.isoformat(),
                "--days",
                "1",
                "--stream",
                stream,
                "--require-accepted-prism-windows",
                "--hierarchical",
                "--work-directory",
                str(scratch),
            ],
            project,
        )
        publication = destination / day.strftime("%Y/%m") / f"{day:%Y%m%d}.LDASIN_DOMAIN1"
        validate_publication(publication, day)
        stamp = day.strftime("%Y%m%d")
        for path in (staging / day.strftime("%Y/%m")).glob(f"{stamp}.*"):
            path.unlink()
        day += timedelta(days=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
