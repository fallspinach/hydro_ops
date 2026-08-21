#!/usr/bin/env python3
"""SLURM entry point for local Stage-IV GRIB2-to-NetCDF conversion."""

from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    python = os.environ.get("HYDRO_OPS_PYTHON", sys.executable)
    command = [python, "-m", "hydro_ops.cli", "convert", "stage4", *sys.argv[1:]]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
