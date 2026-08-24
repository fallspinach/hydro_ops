#!/usr/bin/env python3
#SBATCH --job-name=forcing-compress
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=48:00:00
"""Compress existing forcing conversion files atomically."""

from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    python = os.environ.get("HYDRO_OPS_PYTHON", sys.executable)
    return subprocess.run(
        [python, "bin/compress_forcing_netcdf.py", "--jobs", "4"], check=False
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
