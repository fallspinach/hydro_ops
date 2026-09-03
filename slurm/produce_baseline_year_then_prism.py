#!/usr/bin/env python3
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --time=48:00:00
"""Converge one baseline year in monthly arrays, then submit stable PRISM."""

from __future__ import annotations

import calendar
import os
import re
import subprocess
import sys
import time
from pathlib import Path


def run(command: list[str], project: Path) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command, cwd=project, check=False, capture_output=True, text=True
    )
    print(completed.stdout, end="", flush=True)
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr, flush=True)
    return completed


def wait_for_job(job_id: str, project: Path) -> None:
    while True:
        status = subprocess.run(
            ["squeue", "--noheader", "--jobs", job_id],
            cwd=project,
            check=False,
            capture_output=True,
            text=True,
        )
        if not status.stdout.strip():
            return
        time.sleep(60)


def submit_with_quota_retry(command: list[str], project: Path) -> subprocess.CompletedProcess[str]:
    while True:
        completed = run(command, project)
        if completed.returncode == 0:
            return completed
        message = completed.stdout + completed.stderr
        if "MaxSubmitJobsPerAccount" not in message:
            raise RuntimeError(f"Submission failed with exit code {completed.returncode}")
        print("submission quota full; retrying in 60 seconds", flush=True)
        time.sleep(60)


def converge_month(
    project: Path, python: str, year: int, month: int, concurrency: int
) -> None:
    last = calendar.monthrange(year, month)[1]
    command = [
        python,
        "bin/submit_forcing_days.py",
        "--start",
        f"{year:04d}-{month:02d}-01",
        "--end",
        f"{year:04d}-{month:02d}-{last:02d}",
        "--missing-only",
        "--max-concurrent",
        str(concurrency),
        "--cpus-per-task",
        "12",
        "--tmp-mb",
        "120000",
        "--job-name",
        f"nwm-baseline-build-{year:04d}-{month:02d}",
    ]
    while True:
        completed = submit_with_quota_retry(command, project)
        match = re.search(r"Submitted batch job (\d+)", completed.stdout)
        if match is None:
            if "eligible_days=0" in completed.stdout:
                return
            raise RuntimeError("Baseline submitter returned no job ID or completion marker")
        wait_for_job(match.group(1), project)


def main() -> int:
    project = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"])
    python = os.environ.get("HYDRO_OPS_PYTHON", sys.executable)
    year = int(os.environ["HYDRO_OPS_BASELINE_YEAR"])
    concurrency = int(os.environ.get("HYDRO_OPS_BASELINE_MAX_CONCURRENT", "4"))
    for month in range(1, 13):
        converge_month(project, python, year, month, concurrency)
    prism = [
        python,
        "bin/submit_prism_forcing_updates.py",
        "--stream",
        "retro",
        "--start",
        f"{year:04d}-01-01",
        "--end",
        f"{year:04d}-12-31",
        "--max-concurrent",
        "1",
        "--cpus-per-task",
        "64",
        "--job-name",
        f"prism-retro-{year:04d}-daily",
        "--force",
    ]
    submit_with_quota_retry(prism, project)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
