#!/usr/bin/env python3
#SBATCH --job-name=forcing-production
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=64G
#SBATCH --time=04:00:00
"""SLURM array entry point: one complete forcing hour per task."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta


def main() -> int:
    start = datetime.strptime(os.environ["HYDRO_OPS_START"], "%Y%m%d%H").replace(tzinfo=UTC)
    index = int(os.environ["SLURM_ARRAY_TASK_ID"])
    valid = start + timedelta(hours=index)
    python = os.environ.get("HYDRO_OPS_PYTHON", sys.executable)
    scratch = (
        f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}"
        "/forcing-production"
    )
    command = [
        python, "bin/produce_forcing_range.py", "--start", valid.strftime("%Y%m%d%H"),
        "--end", valid.strftime("%Y%m%d%H"), "--work-directory", scratch,
    ]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
