#!/usr/bin/env python3
# SBATCH --job-name=monthly-ppt-audit
# SBATCH --nodes=1
# SBATCH --ntasks=1
# SBATCH --cpus-per-task=64
# SBATCH --time=04:00:00
"""SLURM entry point for monthly precipitation provenance tracing."""

from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    names = ("YEAR", "MONTH", "COMPLETE_ROOT", "DIAGNOSTICS", "PRISM", "WEIGHTS", "OUTPUT")
    missing = [name for name in names if not os.environ.get(f"HYDRO_OPS_{name}")]
    if missing:
        raise RuntimeError(f"Missing HYDRO_OPS variables: {', '.join(missing)}")
    command = [
        os.environ.get("HYDRO_OPS_PYTHON", sys.executable),
        "bin/analyze_monthly_precipitation_provenance.py",
    ]
    for name in names:
        command.extend([f"--{name.lower().replace('_', '-')}", os.environ[f"HYDRO_OPS_{name}"]])
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
