#!/usr/bin/env python3
#SBATCH --job-name=forcing-validation
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --time=01:00:00
"""SLURM entry point for one full daily-forcing acceptance scan."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    task_file = Path(os.environ["HYDRO_OPS_VALIDATION_TASK_FILE"])
    task = json.loads(task_file.read_text().splitlines()[int(os.environ["SLURM_ARRAY_TASK_ID"])])
    command = [
        os.environ.get("HYDRO_OPS_PYTHON", sys.executable),
        "bin/validate_nwm_forcing_day.py",
        task["path"],
        "--report",
        task["report"],
        "--scenario",
        task["scenario"],
        "--stream",
        task["stream"],
        "--slurm-job-id",
        f"{os.environ['SLURM_ARRAY_JOB_ID']}_{os.environ['SLURM_ARRAY_TASK_ID']}",
    ]
    if task.get("revision"):
        command.extend(["--expected-revision", task["revision"]])
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
