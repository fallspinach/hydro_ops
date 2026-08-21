#!/usr/bin/env python3
"""SLURM entry point for NOAA HRRR forcing downloads."""

from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    python = os.environ.get("HYDRO_OPS_PYTHON", sys.executable)
    command = [python, "-m", "hydro_ops.cli", "download", "hrrr", *sys.argv[1:]]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
