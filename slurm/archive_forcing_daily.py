#!/usr/bin/env python3
#SBATCH --job-name=forcing-daily
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=64G
#SBATCH --time=24:00:00
"""Create checksum-verified daily forcing archives."""

from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    python = os.environ.get("HYDRO_OPS_PYTHON", sys.executable)
    arguments = list(sys.argv[1:])
    if "--jobs" not in arguments:
        arguments.extend(("--jobs", os.environ.get("SLURM_CPUS_PER_TASK", "1")))
    return subprocess.run(
        [python, "bin/archive_forcing_daily.py", *arguments], check=False
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
