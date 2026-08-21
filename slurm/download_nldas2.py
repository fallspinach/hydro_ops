#!/usr/bin/env python3
# SBATCH --nodes=1
# SBATCH --ntasks=1
# SBATCH --export=ALL
"""SLURM entry point for NLDAS-2 downloads."""

from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    project_root = os.getenv(
        "HYDRO_OPS_PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    python = os.getenv("HYDRO_OPS_PYTHON", sys.executable)
    command = [python, "-m", "hydro_ops.cli", "download", "nldas2", *sys.argv[1:]]
    return subprocess.run(command, cwd=project_root, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
