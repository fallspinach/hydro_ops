#!/usr/bin/env python3
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --time=01:00:00
"""Publish one UTC calendar day from two accepted PRISM constraint windows."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    task_file = Path(os.environ["HYDRO_OPS_PRISM_RECOMBINE_TASK_FILE"])
    task = json.loads(task_file.read_text().splitlines()[int(os.environ["SLURM_ARRAY_TASK_ID"])])
    project = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"])
    python = os.environ.get("HYDRO_OPS_PYTHON", sys.executable)
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}")
    return subprocess.run(
        [
            python,
            str(project / "bin/materialize_calendar_forcing.py"),
            "--input-root",
            task["input_root"],
            "--output-root",
            task["output_root"],
            "--start",
            task["day"],
            "--days",
            "1",
            "--work-directory",
            str(scratch),
            "--stream",
            task["stream"],
            "--require-accepted-prism-windows",
            "--hierarchical",
        ],
        cwd=project,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
