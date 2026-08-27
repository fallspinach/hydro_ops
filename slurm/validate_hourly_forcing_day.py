#!/usr/bin/env python3
#SBATCH --job-name=hourly-forcing-validation
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --time=01:00:00
"""SLURM entry point for one hourly-collection daily acceptance scan."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    task_file = Path(os.environ["HYDRO_OPS_HOURLY_VALIDATION_TASK_FILE"])
    task = json.loads(task_file.read_text().splitlines()[int(os.environ["SLURM_ARRAY_TASK_ID"])])
    command = [
        os.environ.get("HYDRO_OPS_PYTHON", sys.executable),
        "bin/validate_hourly_forcing_day.py",
        task["root"],
        task["day"],
        "--report",
        task["report"],
        "--scenario",
        task["scenario"],
        "--slurm-job-id",
        f"{os.environ['SLURM_ARRAY_JOB_ID']}_{os.environ['SLURM_ARRAY_TASK_ID']}",
    ]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
