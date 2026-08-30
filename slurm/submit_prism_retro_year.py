#!/usr/bin/env python3
#SBATCH --job-name=submit-prism-retro-year
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --time=00:30:00
"""Submit one stable retrospective year after its baseline and layout are ready."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


def main() -> int:
    project = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"])
    python = os.environ.get("HYDRO_OPS_PYTHON", sys.executable)
    year = int(os.environ["HYDRO_OPS_RETRO_YEAR"])
    concurrency = os.environ["HYDRO_OPS_RETRO_MAX_CONCURRENT"]
    command = [
        python,
        str(project / "bin/submit_prism_forcing_updates.py"),
        "--stream",
        "retro",
        "--start",
        f"{year:04d}-01-01",
        "--end",
        f"{year:04d}-12-31",
        "--max-concurrent",
        concurrency,
        "--cpus-per-task",
        "64",
        "--force",
    ]
    completed = subprocess.run(
        command, cwd=project, check=False, capture_output=True, text=True
    )
    print(completed.stdout, end="", flush=True)
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr, flush=True)
    if completed.returncode:
        return completed.returncode
    match = re.search(r"Submitted batch job (\d+)", completed.stdout)
    cleanup = [
        "sbatch",
        "--partition=shared-128",
        "--job-name=cleanup-stable-baseline",
        (
            "--export=ALL,"
            f"HYDRO_OPS_PROJECT_ROOT={project},"
            f"HYDRO_OPS_PYTHON={python},"
            f"HYDRO_OPS_CLEANUP_START={year:04d}-01-01,"
            f"HYDRO_OPS_CLEANUP_END={year:04d}-12-31"
        ),
        f"--output={project}/logs/cleanup-stable-baseline-{year}-%j.out",
        str(project / "slurm/cleanup_stable_baseline.py"),
    ]
    if match:
        cleanup.insert(2, f"--dependency=afterok:{match.group(1)}")
        return subprocess.run(cleanup, cwd=project, check=False).returncode
    return subprocess.run(
        [
            python,
            str(project / "bin/cleanup_stable_baseline.py"),
            "--project-root",
            str(project),
            "--start",
            f"{year:04d}-01-01",
            "--end",
            f"{year:04d}-12-31",
        ],
        cwd=project,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
