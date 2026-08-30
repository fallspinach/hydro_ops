#!/usr/bin/env python3
#SBATCH --job-name=cleanup-stable-baseline
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --time=02:00:00
"""SLURM entry point for verified stable-baseline cleanup."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    project = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"])
    python = os.environ.get("HYDRO_OPS_PYTHON", sys.executable)
    return subprocess.run(
        [
            python,
            str(project / "bin/cleanup_stable_baseline.py"),
            "--project-root",
            str(project),
            "--start",
            os.environ["HYDRO_OPS_CLEANUP_START"],
            "--end",
            os.environ["HYDRO_OPS_CLEANUP_END"],
        ],
        cwd=project,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
