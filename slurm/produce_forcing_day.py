#!/usr/bin/env python3
#SBATCH --job-name=forcing-day
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=12
#SBATCH --time=04:00:00
"""SLURM array entry point: one UTC day per task."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, timedelta


def main() -> int:
    start = date.fromisoformat(os.environ["HYDRO_OPS_START_DAY"])
    day = start + timedelta(days=int(os.environ["SLURM_ARRAY_TASK_ID"]))
    python = os.environ.get("HYDRO_OPS_PYTHON", sys.executable)
    scratch = (
        f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}"
        f"/forcing-day-{day:%Y%m%d}"
    )
    command = [
        python,
        "bin/produce_forcing_day.py",
        "--day",
        day.isoformat(),
        "--work-directory",
        scratch,
        "--assembly-workers",
        os.environ.get("HYDRO_OPS_ASSEMBLY_WORKERS", "4"),
        "--precipitation-remap-workers",
        os.environ.get("HYDRO_OPS_PRECIPITATION_REMAP_WORKERS", "1"),
    ]
    if output_root := os.environ.get("HYDRO_OPS_OUTPUT_ROOT"):
        command.extend(["--output-root", output_root])
    if os.environ.get("HYDRO_OPS_FORCE") == "1":
        command.append("--force")
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
