#!/usr/bin/env python3
# SBATCH --job-name=prism-constrained-day
# SBATCH --nodes=1
# SBATCH --ntasks=1
# SBATCH --cpus-per-task=12
# SBATCH --time=02:00:00
"""SLURM entry point for one complete PRISM-constrained daily forcing file."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    project = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"])
    python = os.environ.get("HYDRO_OPS_PYTHON", sys.executable)
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}")
    if task_file := os.environ.get("HYDRO_OPS_PRISM_TASK_FILE"):
        fields = (
            Path(task_file).read_text().splitlines()[int(os.environ["SLURM_ARRAY_TASK_ID"])].split()
        )
        prism_day, revision = fields
    else:
        prism_day = os.environ["HYDRO_OPS_PRISM_DAY"]
        revision = os.environ.get("HYDRO_OPS_PRISM_REVISION", "early")
    command = [
        python,
        str(project / "bin/produce_prism_constrained_daily.py"),
        "--day",
        prism_day,
        "--complete-root",
        os.environ["HYDRO_OPS_COMPLETE_ROOT"],
        "--output-root",
        os.environ["HYDRO_OPS_OUTPUT_ROOT"],
        "--revision",
        revision,
        "--max-iterations",
        os.environ.get("HYDRO_OPS_PRISM_MAX_ITERATIONS", "80"),
        "--work-directory",
        str(scratch),
        "--archive-access",
        os.environ.get("HYDRO_OPS_ARCHIVE_ACCESS", "direct"),
    ]
    if stream := os.environ.get("HYDRO_OPS_FORCING_STREAM"):
        command.extend(["--stream", stream])
    if os.environ.get("HYDRO_OPS_FORCE") == "1":
        command.append("--force")
    if weights := os.environ.get("HYDRO_OPS_PRECIPITATION_WEIGHTS"):
        command.extend(["--precipitation-weights", weights])
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
