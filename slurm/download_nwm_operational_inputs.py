#!/usr/bin/env python3
#SBATCH --job-name=nwm-domain
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=12:00:00
"""Download and checksum the public operational NWM 3.1 CONUS input bundle."""

from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    python = os.environ.get("HYDRO_OPS_PYTHON", sys.executable)
    return subprocess.run(
        [python, "bin/download_nwm_operational_inputs.py"], check=False
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
