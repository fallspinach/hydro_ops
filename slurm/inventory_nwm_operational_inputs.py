#!/usr/bin/env python3
#SBATCH --job-name=nwm-inventory
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=01:00:00
"""Run the NWM domain compatibility inventory after its download."""

from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    python = os.environ.get("HYDRO_OPS_PYTHON", sys.executable)
    return subprocess.run(
        [python, "bin/inventory_nwm_operational_inputs.py"], check=False
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
